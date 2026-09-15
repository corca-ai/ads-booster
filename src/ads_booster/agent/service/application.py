"""Channel-neutral application service for canonical on-premises Agent Runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from time import monotonic
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from ads_booster.agent.core.ports import CompletionRenderContext, ReasoningProviderV2
from ads_booster.agent.core.registry import (
    CapabilityPolicy,
    ToolRegistrationCatalog,
    ToolRegistry,
)
from ads_booster.agent.runtime import (
    AgentSession,
    ApprovalGrant,
    BoundToolInvocation,
    Budget,
    DeferredToolExecution,
    EffectDisposition,
    MarketingAgentRuntime,
    RuntimeState,
    SqliteSessionStore,
    ToolAdmission,
    ToolCapability,
    ToolReceipt,
    bind_tool_invocation,
    pending_deferred_execution,
    tool_call_payload,
    tool_receipt_from_event,
)
from ads_booster.agent.service.completion_evidence import CompletionEvidenceReader
from ads_booster.agent.service.deferred_failure import FAILURE_TEXT
from ads_booster.agent.service.drive_work import DriveAdmissionConflict
from ads_booster.agent.service.pending_approval import pending_approval
from ads_booster.agent.service.run_limits import (
    ProgressObservation,
    observe_progress,
    outcome_fingerprint,
    progress_evidence_fingerprint,
    remaining_budget,
    reserve_decision,
    stable_failure_fingerprint,
)
from ads_booster.agent.service.run_locks import RunLocks
from ads_booster.agent.service.sqlite_repository import (
    AgentRunConflictError,
    RepositoryAdmission,
    SqliteAgentRunRepository,
)
from ads_booster.agent.service.task_admission import completion_revision_fence, task_at_boundary
from ads_booster.agent.service.task_completion import (
    CompletionContext,
    LegacyV1CompletionAssessor,
    TaskCompletionService,
)
from ads_booster.agent.service.task_drive import (
    DecisionProjectionContext,
    plan_task,
    project_decision,
)
from ads_booster.agent.service.task_input import current_user_message, new_input_after_brand_wait
from ads_booster.agent.service.task_progress import (
    TaskProjection,
    project_task,
    seed_task,
    task_records,
)
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentIntent,
    AgentRecord,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    CapabilitySnapshot,
    ToolApproval,
    ToolExecutionDeferred,
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.knowledge_preparation import (
    BrandUnresolvedPreparation,
    PreparedKnowledgeContext,
    RequiredContextPreparationError,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningDecisionV2,
    ReasoningRequest,
    decode_reasoning_result,
)
from ads_booster.contracts.task_progress import ObligationEvidence, TaskPolicy
from ads_booster.contracts.tool_capability import (
    AUTHENTICATED_SOURCE_AUTHORITY,
    EffectClass,
    ToolDescriptor,
    ToolExecutionResult,
    allows_authenticated_source_approval,
)
from ads_booster.execution_control import checkpoint

_CONTEXT_SELECTION_SCHEMA = "trace.reasoning-context-selection.v1"
_MAX_CONTEXT_RECORDS = 32
_MAX_CONTEXT_BYTES = 48 * 1024
_MAX_STEERING_EVENT_ID = 512
_MAX_STEERING_ACTOR_ID = 160
_MAX_STEERING_NOTE = 20_000
_RUNTIME_SOURCE_AUTHORIZED_DESCRIPTOR_INVALID = "runtime_source_authorized_descriptor_invalid"

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from sqlite3 import Connection

    from ads_booster.agent.core.ports import (
        CompletionRenderer,
        ReasoningProvider,
        ReasoningProviderV2,
        ToolAdapter,
    )
    from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter
    from ads_booster.agent.service.sqlite_repository import RepositoryAfterCommit
    from ads_booster.contracts.task_completion import CompletionAssessment
    from ads_booster.transport.json_types import JsonObject


class CreateAgentRunRequest(ContractModel):
    run_id: str
    tenant_id: str
    goal: AgentGoal
    budget: AgentBudget


@dataclass(slots=True)
class MarketingAgentService:
    run_locks: RunLocks = field(default_factory=RunLocks, init=False, repr=False)
    catalog_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    repository: SqliteAgentRunRepository
    registry: ToolRegistry
    reasoning: ReasoningProvider | ReasoningProviderV2
    tools: Mapping[str, ToolAdapter]
    runtime_store: SqliteSessionStore
    fault_hook: Callable[[str], None] | None = None
    capability_policy: CapabilityPolicy = field(default_factory=CapabilityPolicy)
    runtime: MarketingAgentRuntime = field(default_factory=MarketingAgentRuntime)
    boundary_signal: Callable[[str, str], JsonObject | None] | None = None

    current_context: Callable[[AgentRun, datetime], JsonObject | None] | None = None
    knowledge: KnowledgeServiceAdapter | None = None
    completion: TaskCompletionService | None = None
    renderer: CompletionRenderer | None = None
    task_policy: TaskPolicy = field(default_factory=TaskPolicy)
    drive_admission: Callable[[AgentRun, TaskProjection, datetime], RepositoryAdmission] | None = (
        None
    )
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic_clock: Callable[[], float] = monotonic
    _active_meter: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Fail closed when a selectable descriptor has no execution adapter."""
        if self.runtime_store.database_path != self.repository.database_path:
            raise ValueError("agent_runtime_must_share_canonical_database")
        missing = tuple(
            item.capability_id
            for item in self.registry.descriptors
            if item.enabled and item.readiness.ready and item.capability_id not in self.tools
        )
        if missing:
            raise ValueError("ready_tool_adapter_missing")
        if self.completion is None and not isinstance(self.reasoning, ReasoningProviderV2):
            # Keep the public constructor compatible with v1 providers.  Callers
            # may still explicitly disable completion after construction; the
            # assessment path then retains its fail-closed behavior.
            self.completion = TaskCompletionService(self.repository, LegacyV1CompletionAssessor())

    def install_tool_catalog(
        self,
        catalog: ToolRegistrationCatalog,
        *,
        now: datetime,
    ) -> None:
        """Publish validated descriptor and adapter projections as one service update."""
        with self.catalog_lock:
            candidate = self.registry.with_registrations(catalog.registrations(), now=now)
            self.registry, self.tools = candidate, candidate.adapters

    def create(
        self,
        request: CreateAgentRunRequest,
        *,
        now: datetime,
        admission: RepositoryAdmission | None = None,
    ) -> AgentRun:
        with self.run_locks.hold(request.tenant_id, request.run_id):
            current = self.repository.get(request.tenant_id, request.run_id)
            if current is not None:
                if (
                    current.tenant_id != request.tenant_id
                    or current.goal != request.goal
                    or current.budget != request.budget
                ):
                    raise ValueError("agent_run_idempotency_conflict")
                return self.drive(current.tenant_id, current.run_id, now=now)
            run = self.repository.create(
                AgentRun(
                    schema_version="trace.agent-run.v1",
                    run_id=request.run_id,
                    tenant_id=request.tenant_id,
                    goal=request.goal,
                    budget=request.budget,
                    state=AgentRunState.CREATED,
                    created_at=now,
                    updated_at=now,
                ),
                request_sha256=contract_sha256(request),
                admission=admission,
            )
            return self.drive(run.tenant_id, run.run_id, now=now)

    def stop(self, tenant_id: str, run_id: str, *, now: datetime) -> AgentRun | None:
        """Stop future work after the owning execution has yielded; preserve uncertain effects."""
        with self.run_locks.hold(tenant_id, run_id):
            run = self.repository.get(tenant_id, run_id)
            if run is None or run.state in {
                AgentRunState.STOPPED,
                AgentRunState.COMPLETED,
                AgentRunState.FAILED,
                AgentRunState.AWAITING_RECONCILIATION,
            }:
                return run
            return self._append_step(
                run,
                _step(
                    run,
                    kind=AgentStepKind.STOP,
                    input_sha256=contract_sha256({"run": run_id, "reason": "user_cancelled"}),
                    output_sha256=contract_sha256({"state": "stopped"}),
                    now=now,
                ),
                state=AgentRunState.STOPPED,
                expected_revision=run.revision,
                records=(
                    _record(
                        run,
                        record_id=f"{run.run_id}:cancelled:{run.revision}",
                        kind=AgentRecordKind.EVIDENCE,
                        payload={"schema_version": "trace.work-cancelled.v1"},
                        now=now,
                    ),
                ),
            )

    def drive(  # noqa: PLR0911 - distinct persisted recovery boundaries.
        self, tenant_id: str, run_id: str, *, now: datetime
    ) -> AgentRun:
        """Advance persisted transitions within a bounded provider/time slice."""
        with self.run_locks.hold(tenant_id, run_id):
            run = self._required_run(tenant_id, run_id)
            initial = self._task(run).checkpoint
            started = self.monotonic_clock()
            self._active_meter = started
            try:
                while True:
                    instant = self.clock()
                    task = self._task(run)
                    if (
                        task.checkpoint.decision_calls - initial.decision_calls
                        >= initial.policy.slice_provider_calls
                        or self.monotonic_clock() - started >= initial.policy.slice_seconds
                    ):
                        return run
                    try:
                        advanced = self._advance(run, now=max(now, instant, run.updated_at))
                    except DriveAdmissionConflict:
                        return self._required_run(tenant_id, run_id)
                    except AgentRunConflictError:
                        current = self._required_run(tenant_id, run_id)
                        if current.revision != run.revision:
                            return current
                        raise
                    if (
                        advanced.revision == run.revision
                        or advanced.state is not AgentRunState.RUNNING
                    ):
                        return advanced
                    run = advanced
            finally:
                self._active_meter = None

    def _advance(self, run: AgentRun, *, now: datetime) -> AgentRun:  # noqa: PLR0911
        """Recover or perform exactly one persisted orchestration transition."""
        with self.run_locks.hold(run.tenant_id, run.run_id):
            tenant_id, run_id = run.tenant_id, run.run_id
            if run.state in {AgentRunState.AWAITING_TOOL, AgentRunState.AWAITING_RECONCILIATION}:
                if run.state is AgentRunState.AWAITING_RECONCILIATION:
                    invocation = self._execution_invocation(tenant_id, run_id)
                    if self._deferred_for_invocation(run, invocation) is None:
                        return run
                return self._resume_execution(run, now=now)
            if run.state is not AgentRunState.RUNNING and run.state is not AgentRunState.CREATED:
                return run
            steps = self.repository.steps(tenant_id, run_id)
            if steps and steps[-1].kind is AgentStepKind.EXECUTE:
                return self._resume_execution(run, now=now)
            if steps and steps[-1].kind is AgentStepKind.VERIFY:
                return self._resume_verified_tool(run, now=now)
            if steps and steps[-1].kind is AgentStepKind.APPROVE:
                return self._resume_approved_invocation(run, now=now)
            task = project_task(run, self.repository.records(tenant_id, run_id))
            if task.checkpoint.next_action == "assess":
                return self._assess_completion(run, task, now=now)
            if steps and steps[-1].kind in {AgentStepKind.PLAN, AgentStepKind.REPLAN}:
                return self._resume_planned_decision(run, now=now)
            evidence = tuple(
                record.payload
                for record in self.repository.records(tenant_id, run_id)
                if record.kind is AgentRecordKind.EVIDENCE
            )
            return self._plan(run, evidence=evidence[-1:], now=now)

    def _resume_planned_decision(self, run: AgentRun, *, now: datetime) -> AgentRun:
        records = self.repository.records(run.tenant_id, run.run_id)
        intent_record = next(
            (item for item in reversed(records) if item.kind is AgentRecordKind.INTENT), None
        )
        reasoning_record = next(
            (item for item in reversed(records) if item.kind is AgentRecordKind.REASONING), None
        )
        snapshot_record = next(
            (
                item
                for item in reversed(records)
                if item.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
            ),
            None,
        )
        if intent_record is None or reasoning_record is None or snapshot_record is None:
            raise ValueError("planned_decision_recovery_records_missing")
        intent = AgentIntent.model_validate(intent_record.payload)
        reasoning = decode_reasoning_result(reasoning_record.payload)
        snapshot = CapabilitySnapshot.model_validate(snapshot_record.payload)
        decision = reasoning.decision
        if (
            intent.action != "invoke_tool"
            or decision.action != "invoke_tool"
            or decision.capability_id is None
            or decision.tool_input is None
            or intent.capability_id != decision.capability_id
        ):
            raise ValueError("planned_decision_recovery_invalid")
        return self._execute_tool(
            run,
            intent=intent,
            capability_id=decision.capability_id,
            tool_input=decision.tool_input,
            snapshot=snapshot,
            now=now,
        )

    def _resume_execution(self, run: AgentRun, *, now: datetime) -> AgentRun:
        invocation = self._execution_invocation(run.tenant_id, run.run_id)
        descriptor = self._descriptor_for_invocation(run.tenant_id, run.run_id, invocation)
        session = self.runtime_store.load(run.run_id)
        acknowledgement = self._deferred_for_invocation(run, invocation)
        if acknowledgement is not None:
            completion = self._deferred_completion(run, acknowledgement.operation_id)
            if completion is not None:
                return self.complete_deferred(
                    run.tenant_id,
                    run.run_id,
                    operation_id=acknowledgement.operation_id,
                    result=ToolExecutionResult.model_validate(completion.payload["result"]),
                    now=now,
                )
            return self._await_deferred(run, invocation, acknowledgement, now=now)
        if (
            session is not None
            and invocation.idempotency_key in session.dispatched_idempotency_keys
            and (
                session.pending_invocation is None
                or session.execution_started
                or session.state is RuntimeState.AWAITING_RECONCILIATION
            )
        ):
            if (
                session.pending_invocation is not None
                and session.execution_started
                and session.state is RuntimeState.EXECUTING
            ):
                _ = self.runtime.reconcile_interrupted_execution(
                    self.runtime_store, session, now=now
                )
            return self._mark_reconciliation(run, contract_sha256(invocation), now=now)
        approval = self._approval_for_invocation(run.tenant_id, run.run_id, invocation, now=now)
        return self._dispatch_tool(
            run,
            invocation=invocation,
            descriptor=descriptor,
            approval=approval,
            now=now,
            persist_invocation=False,
            admitted_already=True,
        )

    def _resume_verified_tool(self, run: AgentRun, *, now: datetime) -> AgentRun:
        records = self.repository.records(run.tenant_id, run.run_id)
        receipt_record = next(
            (item for item in reversed(records) if item.kind is AgentRecordKind.RECEIPT), None
        )
        evidence_record = next(
            (
                item
                for item in reversed(records)
                if item.payload_schema_version == "trace.tool-output-evidence.v1"
                and receipt_record is not None
                and item.payload.get("receipt_sha256") == receipt_record.payload_sha256
            ),
            None,
        )
        if receipt_record is None or evidence_record is None:
            raise ValueError("verified_tool_recovery_records_missing")
        receipt = ToolReceiptRecord.model_validate(receipt_record.payload)
        return self._evaluate_tool_result(run, receipt, evidence_record.payload, now=now)

    def _resume_approved_invocation(self, run: AgentRun, *, now: datetime) -> AgentRun:
        records = self.repository.records(run.tenant_id, run.run_id)
        approval_record = next(r for r in reversed(records) if r.kind is AgentRecordKind.APPROVAL)
        invocation = next(
            ToolInvocation.model_validate(r.payload)
            for r in records
            if r.kind is AgentRecordKind.INVOCATION
            and contract_sha256(r.payload) == approval_record.payload["invocation_sha256"]
        )
        descriptor = self._descriptor_for_invocation(run.tenant_id, run.run_id, invocation)
        approval = self._approval_for_invocation(run.tenant_id, run.run_id, invocation, now=now)
        return self._dispatch_tool(
            run,
            invocation=invocation,
            descriptor=descriptor,
            approval=approval,
            now=now,
            persist_invocation=False,
        )

    def submit_input(
        self,
        tenant_id: str,
        run_id: str,
        evidence: JsonObject,
        *,
        now: datetime,
        admission: RepositoryAdmission | None = None,
    ) -> AgentRun:
        with self.run_locks.hold(tenant_id, run_id):
            run = self._required_run(tenant_id, run_id)
            if run.state is not AgentRunState.AWAITING_INPUT:
                raise ValueError("agent_run_not_awaiting_input")
            evidence_payload: JsonObject = {
                "schema_version": "trace.agent-input-evidence.v1",
                "evidence": evidence,
            }
            evidence_sha256 = contract_sha256(evidence_payload)
            record = _record(
                run,
                record_id=f"{run.run_id}:input:{run.revision}",
                kind=AgentRecordKind.EVIDENCE,
                payload=evidence_payload,
                now=now,
            )
            resumed = self._append_step(
                run,
                _step(
                    run,
                    kind=AgentStepKind.OBSERVE,
                    input_sha256=evidence_sha256,
                    output_sha256=evidence_sha256,
                    now=now,
                ),
                state=AgentRunState.RUNNING,
                expected_revision=run.revision,
                records=(record,),
                admission=admission,
            )
            return self.drive(resumed.tenant_id, resumed.run_id, now=now)

    def decide_approval(  # noqa: PLR0913 - exact approval identity and expiry stay explicit.
        self,
        tenant_id: str,
        run_id: str,
        *,
        approver_id: str,
        granted: bool,
        now: datetime,
        expires_at: datetime | None = None,
        expected_invocation_sha256: str | None = None,
        request_event_id: str | None = None,
        request_text_sha256: str | None = None,
    ) -> AgentRun:
        """Resolve one exact pending invocation and dispatch only a valid grant."""
        with self.run_locks.hold(tenant_id, run_id):
            run = self._required_run(tenant_id, run_id)
            if run.state is not AgentRunState.AWAITING_APPROVAL:
                raise ValueError("agent_run_not_awaiting_approval")
            invocation = self.pending_approval(tenant_id, run_id)
            if invocation is None:
                raise ValueError("pending_tool_invocation_missing")
            if (
                expected_invocation_sha256 is not None
                and contract_sha256(invocation) != expected_invocation_sha256
            ):
                raise ValueError("agent_approval_invocation_changed")
            if granted and expires_at is None:
                raise ValueError("approval_expiry_required")
            descriptor = self._descriptor_for_invocation(tenant_id, run_id, invocation)
            if granted:
                _ = self.registry.require_current_dispatch(
                    descriptor, policy=self.capability_policy, now=now
                )
                if descriptor.capability_id not in self.tools:
                    raise ValueError("tool_dispatch_adapter_unavailable")
            approval = ToolApproval(
                schema_version="trace.tool-approval.v1",
                approval_id=f"{run_id}:approval:{run.revision}",
                invocation_sha256=contract_sha256(invocation),
                approver_id=approver_id,
                decision="granted" if granted else "rejected",
                expires_at=expires_at,
                decided_at=now,
                request_event_id=request_event_id,
                request_text_sha256=request_text_sha256,
            )
            approval_sha256 = contract_sha256(approval)
            decided = self._append_step(
                run,
                _step(
                    run,
                    kind=AgentStepKind.APPROVE,
                    input_sha256=contract_sha256(invocation),
                    output_sha256=approval_sha256,
                    now=now,
                ),
                state=AgentRunState.RUNNING if granted else AgentRunState.STOPPED,
                expected_revision=run.revision,
                records=(
                    _record(
                        run,
                        record_id=approval.approval_id,
                        kind=AgentRecordKind.APPROVAL,
                        payload=approval.model_dump(mode="json"),
                        now=now,
                    ),
                ),
            )
            if not granted:
                return decided
            self._fault("approval_committed")
            return self.drive(decided.tenant_id, decided.run_id, now=now)

    def _plan(
        self,
        run: AgentRun,
        *,
        evidence: tuple[JsonObject, ...],
        now: datetime,
    ) -> AgentRun:
        checkpoint("내용을 살펴보고 답변을 작성하고 있습니다")
        interrupted = self._pause_for_signal(run, now=now)
        if interrupted is not None:
            return interrupted
        # All callers, including restart recovery, project the same canonical history.
        # The caller's last observation is only a trigger, never the whole context.
        evidence, context_selection = self._select_context(run)
        user_message = current_user_message(run, self.repository.records(run.tenant_id, run.run_id))
        snapshot = self.registry.snapshot_for_plan(
            snapshot_id=f"{run.run_id}:capabilities:{run.revision}",
            run_id=run.run_id,
            remaining_tool_calls=max(
                0, run.budget.max_tool_calls - self._tool_calls(run.tenant_id, run.run_id)
            ),
            remaining_cost_units=max(
                0, run.budget.max_cost_units - self._spent_cost(run.tenant_id, run.run_id)
            ),
            policy=self.capability_policy,
            now=now,
        )
        # Retrieval follows admitted intent too, even when its evidence was compacted.
        knowledge_query = user_message[:4000] + (
            "\n" + run.goal.objective[:4000] if user_message != run.goal.objective else ""
        )
        prepared_context: PreparedKnowledgeContext | None = None
        if self.knowledge is not None:
            snapshot = self.knowledge.filter_snapshot(run.run_id, snapshot)
            preparation = self.knowledge.prepare(
                run,
                snapshot,
                now=now,
                query=knowledge_query,
                action_kind=(
                    KnowledgeActionKind.TEAM_CHAT
                    if new_input_after_brand_wait(
                        self.repository.records(run.tenant_id, run.run_id)
                    )
                    else None
                ),
            )
            if isinstance(
                preparation,
                RequiredContextPreparationError | BrandUnresolvedPreparation,
            ):
                return self._await_knowledge_input(run, preparation, snapshot, now=now)
            prepared_context = preparation
        snapshot_record = _record(
            run,
            record_id=snapshot.snapshot_id,
            kind=AgentRecordKind.CAPABILITY_SNAPSHOT,
            payload=snapshot.model_dump(mode="json"),
            now=now,
        )
        context_records = (
            ()
            if prepared_context is None
            else (
                _record(
                    run,
                    record_id=f"{run.run_id}:knowledge-context:{run.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload={
                        "schema_version": "trace.prepared-knowledge-context-record.v1",
                        "prepared_context": prepared_context.model_dump(mode="json"),
                    },
                    now=now,
                ),
            )
        )
        task = self._task(run)
        reserved = reserve_decision(task.checkpoint)
        if reserved is None:
            return self._block_task(run, task, "decision_budget_exhausted", now=now)
        task = TaskProjection(task.spec, reserved.model_copy(update={"next_action": "plan"}))
        observed = self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.OBSERVE,
                input_sha256=contract_sha256(run.goal),
                output_sha256=snapshot.digest,
                now=now,
            ),
            state=AgentRunState.RUNNING,
            expected_revision=run.revision,
            records=(
                snapshot_record,
                _record(
                    run,
                    record_id=f"{run.run_id}:context:{run.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload=context_selection,
                    now=now,
                ),
                *context_records,
                *task_records(run, task, now),
            ),
        )
        current_context = None if self.current_context is None else self.current_context(run, now)
        if current_context is not None:
            evidence = (*evidence, current_context)
        pending = self.pending_approval(run.tenant_id, run.run_id)
        reasoning_request = ReasoningRequest(
            schema_version="trace.reasoning-request.v1",
            run_id=run.run_id,
            phase="plan" if not evidence else "replan",
            goal=run.goal,
            current_user_message=user_message,
            pending_approval=(
                None
                if pending is None
                else {
                    "invocation": pending.model_dump(mode="json"),
                    "capability_id": self._descriptor_for_invocation(
                        run.tenant_id, run.run_id, pending
                    ).capability_id,
                }
            ),
            capability_snapshot=snapshot,
            evidence=evidence,
            remaining_tool_calls=max(
                0, run.budget.max_tool_calls - self._tool_calls(run.tenant_id, run.run_id)
            ),
            remaining_cost_units=max(
                0, run.budget.max_cost_units - self._spent_cost(run.tenant_id, run.run_id)
            ),
            prepared_context=prepared_context,
        )
        reasoning_result = plan_task(
            self.reasoning,
            reasoning_request,
            task,
            self.repository.records(run.tenant_id, run.run_id),
        )
        checkpoint("다음 작업을 확인하고 있습니다")
        decision = reasoning_result.decision
        remaining = remaining_budget(observed, self.repository.records(run.tenant_id, run.run_id))
        if (
            decision.action == "invoke_tool"
            and not any(
                item.capability_id == decision.capability_id for item in snapshot.descriptors
            )
            and (remaining.tool_calls == 0 or remaining.cost_units == 0)
        ):
            return self._block_task(observed, task, "tool_budget_exhausted", now=now)
        self._validate_reasoning_decision(snapshot, decision)
        if (
            self.knowledge is not None
            and prepared_context is not None
            and decision.proposed_action_kind is not None
            and (
                decision.proposed_action_kind is not prepared_context.request.action_kind
                or decision.proposed_brand_ref != prepared_context.request.brand_ref
            )
        ):
            rebound = self.knowledge.prepare(
                observed,
                snapshot,
                now=now,
                action_kind=decision.proposed_action_kind,
                brand_id=decision.proposed_brand_ref,
                query=knowledge_query,
            )
            return (
                self._await_knowledge_input(observed, rebound, snapshot, now=now)
                if isinstance(rebound, RequiredContextPreparationError | BrandUnresolvedPreparation)
                else observed
            )
        intent = AgentIntent(
            schema_version="trace.agent-intent.v1",
            intent_id=f"{run.run_id}:intent:{observed.revision}",
            run_id=run.run_id,
            step_id=f"{run.run_id}:step:{observed.revision}",
            action=decision.action,
            capability_id=decision.capability_id,
            evidence_sha256s=tuple(contract_sha256(item) for item in evidence),
            expected_outcome=decision.expected_outcome,
            reasoning_summary=decision.reasoning_summary,
        )
        intent_sha256 = contract_sha256(intent)
        intent_record = _record(
            observed,
            record_id=intent.intent_id,
            kind=AgentRecordKind.INTENT,
            payload=intent.model_dump(mode="json"),
            now=now,
        )
        reasoning_record = _record(
            observed,
            record_id=f"{run.run_id}:reasoning:{observed.revision}",
            kind=AgentRecordKind.REASONING,
            payload=reasoning_result.model_dump(mode="json"),
            now=now,
        )
        next_state = {
            "stop": AgentRunState.RUNNING,
            "request_input": AgentRunState.AWAITING_INPUT,
            "invoke_tool": AgentRunState.RUNNING,
        }[decision.action]
        records = self.repository.records(run.tenant_id, run.run_id)
        selected_sha256s = {contract_sha256(item) for item in evidence}
        reader = CompletionEvidenceReader(self.repository)
        selected_evidence = tuple(
            reader.read(observed, record)
            for record in records
            if record.payload_sha256 in selected_sha256s
            and record.payload_schema_version == "trace.tool-output-evidence.v1"
        )
        task = project_decision(
            task,
            decision,
            DecisionProjectionContext(observed, selected_evidence),
        )
        if (
            decision.action != "invoke_tool"
            and decision.pending_approval_action == "preserve"
            and pending is not None
        ):
            next_state = AgentRunState.AWAITING_APPROVAL
        planned = self._append_step(
            observed,
            _step(
                observed,
                kind=AgentStepKind.REPLAN if evidence else AgentStepKind.PLAN,
                input_sha256=snapshot.digest,
                output_sha256=intent_sha256,
                now=now,
            ),
            state=next_state,
            expected_revision=observed.revision,
            records=(intent_record, reasoning_record, *task_records(observed, task, now)),
        )
        self._fault("plan_committed")
        return planned

    def _task(self, run: AgentRun) -> TaskProjection:
        records = self.repository.records(run.tenant_id, run.run_id)
        if not any(
            item.payload_schema_version == "trace.task-checkpoint.v1"
            and item.record_id.startswith(f"task:{item.payload_sha256}:")
            for item in records
        ):
            return seed_task(run, policy=self.task_policy)
        return project_task(run, records)

    def _append_step(  # noqa: PLR0913 - canonical CAS and transaction callback bindings stay explicit.
        self,
        run: AgentRun,
        step: AgentStep,
        *,
        state: AgentRunState,
        expected_revision: int,
        records: tuple[AgentRecord, ...] = (),
        blocked_reason: str | None = None,
        admission: RepositoryAdmission | None = None,
        after_commit: RepositoryAfterCommit | None = None,
    ) -> AgentRun:
        prior = self._task(run)
        task = project_task(run, (*self.repository.records(run.tenant_id, run.run_id), *records))
        if not any(item.record_id.startswith("task:") for item in records) and not any(
            item.record_id.startswith("task:")
            for item in self.repository.records(run.tenant_id, run.run_id)
        ):
            task = prior
        task = task_at_boundary(
            task, state, blocked_reason or task.checkpoint.wait_reason or state.value
        )
        measured_at = None if self._active_meter is None else self.monotonic_clock()
        if measured_at is not None and self._active_meter is not None:
            previous = (
                prior.checkpoint.active_elapsed_ms
                if prior.checkpoint.segment_id == task.checkpoint.segment_id
                else 0
            )
            task = TaskProjection(
                task.spec,
                task.checkpoint.model_copy(
                    update={
                        "active_elapsed_ms": max(previous, task.checkpoint.active_elapsed_ms)
                        + max(0, int((measured_at - self._active_meter) * 1000)),
                    }
                ),
            )
        canonical = tuple(item for item in records if not item.record_id.startswith("task:"))
        queue = (
            None
            if self.drive_admission is None
            else self.drive_admission(run, task, step.occurred_at)
        )
        fence = completion_revision_fence(run, prior) if state is AgentRunState.COMPLETED else None

        def admit(connection: Connection) -> None:
            if fence is not None:
                fence(connection)
            if admission is not None:
                admission(connection)
            if queue is not None:
                queue(connection)

        updated = self.repository.append_step(
            run,
            step,
            state=state,
            expected_revision=expected_revision,
            records=(*canonical, *task_records(run, task, step.occurred_at)),
            blocked_reason=blocked_reason,
            admission=admit,
            after_commit=after_commit,
        )
        if measured_at is not None:
            self._active_meter = measured_at
        return updated

    def _save_task(
        self,
        run: AgentRun,
        task: TaskProjection,
        *,
        now: datetime,
        state: AgentRunState = AgentRunState.RUNNING,
        records: tuple[AgentRecord, ...] = (),
    ) -> AgentRun:
        return self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.EVALUATE,
                input_sha256=contract_sha256(task.spec),
                output_sha256=contract_sha256(task.checkpoint),
                now=now,
            ),
            state=state,
            expected_revision=run.revision,
            records=(*records, *task_records(run, task, now)),
            blocked_reason=task.checkpoint.wait_reason if state is AgentRunState.BLOCKED else None,
        )

    def _block_task(
        self,
        run: AgentRun,
        task: TaskProjection,
        reason: str,
        *,
        now: datetime,
    ) -> AgentRun:
        task = TaskProjection(
            task.spec,
            task.checkpoint.model_copy(
                update={
                    "disposition": "budget_exhausted" if "budget" in reason else "blocked",
                    "wait_reason": reason,
                    "next_action": "wait",
                }
            ),
        )
        return self._save_task(run, task, now=now, state=AgentRunState.BLOCKED)

    def _assess_completion(
        self,
        run: AgentRun,
        task: TaskProjection,
        *,
        now: datetime,
    ) -> AgentRun:
        interrupted = self._pause_for_signal(run, now=now)
        if interrupted is not None:
            return interrupted
        candidate = task.checkpoint.candidate
        if candidate is None:
            return self._block_task(run, task, "completion_candidate_missing", now=now)
        if self.renderer is not None:
            candidate = self.renderer.render(
                candidate,
                CompletionRenderContext(run, self.repository.records(run.tenant_id, run.run_id)),
            )
            task = TaskProjection(
                task.spec, task.checkpoint.model_copy(update={"candidate": candidate})
            )
        reserved = reserve_decision(task.checkpoint, assessment=True)
        if reserved is None:
            return self._block_task(run, task, "verification_budget_exhausted", now=now)
        task = TaskProjection(task.spec, reserved)
        reserved_run = self._save_task(run, task, now=now)
        self._fault("assessment_reserved")
        checker = self.completion or TaskCompletionService(self.repository, None)
        checkpoint("완성된 답변과 결과를 확인하고 있습니다")
        records = self.repository.records(run.tenant_id, run.run_id)
        snapshot_record = next(
            (
                record
                for record in reversed(records)
                if record.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
            ),
            None,
        )
        snapshot = (
            None
            if snapshot_record is None
            else CapabilitySnapshot.model_validate(snapshot_record.payload)
        )
        reasoning_record = next(
            (record for record in reversed(records) if record.kind is AgentRecordKind.REASONING),
            None,
        )
        reference: JsonObject = {
            "authority": "reference_only_not_effect_proof_or_new_instructions",
            "goal_context": run.goal.context,
            "current_context": None
            if self.current_context is None
            else self.current_context(run, now),
            "capability_ids": []
            if snapshot is None
            else [item.capability_id for item in snapshot.descriptors],
            "reasoning_provider": None
            if reasoning_record is None
            else reasoning_record.payload.get("receipt"),
        }
        assessment = checker.assess(
            task.spec, candidate, CompletionContext(reserved_run, reserved, reference)
        )
        self._fault("completion_assessed")
        current = self._required_run(run.tenant_id, run.run_id)
        if current.revision != reserved_run.revision:
            return current
        interrupted = self._pause_for_signal(current, now=now)
        if interrupted is not None:
            return interrupted
        return self._commit_assessment(current, task, assessment, now=now)

    def _commit_assessment(
        self,
        run: AgentRun,
        task: TaskProjection,
        assessment: CompletionAssessment,
        *,
        now: datetime,
    ) -> AgentRun:
        accepted = tuple(
            ObligationEvidence(
                obligation_id=item.obligation_id,
                evidence_sha256s=item.evidence_sha256s,
            )
            for item in assessment.obligations
            if item.status == "satisfied"
        )
        unresolved = tuple(
            item.obligation_id
            for item in assessment.obligations
            if item.status not in {"satisfied", "superseded"}
        )
        match assessment.disposition:
            case "satisfied":
                disposition, next_action, state = "satisfied", "done", AgentRunState.COMPLETED
            case "continue":
                disposition, next_action, state = "active", "plan", AgentRunState.RUNNING
            case "waiting":
                disposition, next_action, state = "waiting", "wait", AgentRunState.AWAITING_INPUT
            case "blocked":
                disposition, next_action, state = "blocked", "wait", AgentRunState.BLOCKED
        task = TaskProjection(
            task.spec,
            task.checkpoint.model_copy(
                update={
                    "disposition": disposition,
                    "next_action": next_action,
                    "accepted_evidence": accepted,
                    "unresolved_obligation_ids": unresolved,
                    "wait_reason": None if state is AgentRunState.COMPLETED else assessment.reason,
                }
            ),
        )
        self._fault("before_completion_commit")
        return self._save_task(
            run,
            task,
            now=now,
            state=state,
            records=(
                _record(
                    run,
                    record_id=f"{run.run_id}:assessment:{run.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload=assessment.model_dump(mode="json"),
                    now=now,
                ),
            ),
        )

    def _select_context(self, run: AgentRun) -> tuple[tuple[JsonObject, ...], JsonObject]:
        """Bound the provider projection; retain source records and selection provenance."""
        sources = [
            record
            for record in self.repository.records(run.tenant_id, run.run_id)
            if record.kind is AgentRecordKind.EVIDENCE
            and record.payload_schema_version
            not in {
                _CONTEXT_SELECTION_SCHEMA,
                "trace.tool-deferred.v1",
                "trace.tool-completion.v1",
                # Knowledge must be reselected under current authority, never replayed
                # through the general conversation projection after invalidation.
                "trace.prepared-knowledge-context-record.v1",
                "trace.task-spec.v1",
                "trace.task-checkpoint.v1",
                "trace.task-completion.v1",
            }
        ]
        # Keep the latest observation first, then recent human constraints ahead of
        # tool output. Restore chronological order so corrections stay after originals.
        order = sorted(
            range(len(sources)),
            key=lambda index: (
                index == len(sources) - 1,
                sources[index].payload_schema_version
                in {
                    "trace.agent-input-evidence.v1",
                    "trace.work-continuation.v1",
                    "trace.work-interruption.v1",
                },
                index,
            ),
            reverse=True,
        )
        selected: list[int] = []
        selected_bytes = 0
        for index in order:
            size = len(
                json.dumps(
                    sources[index].payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
            if len(selected) < _MAX_CONTEXT_RECORDS and selected_bytes + size <= _MAX_CONTEXT_BYTES:
                selected.append(index)
                selected_bytes += size
        selected.sort()
        evidence = tuple(sources[index].payload for index in selected)
        omitted_count = len(sources) - len(selected)
        selection: JsonObject = {
            "schema_version": _CONTEXT_SELECTION_SCHEMA,
            "selected_sha256s": [sources[index].payload_sha256 for index in selected],
            "selected_bytes": selected_bytes,
            "omitted_count": omitted_count,
            "max_records": _MAX_CONTEXT_RECORDS,
            "max_bytes": _MAX_CONTEXT_BYTES,
            "policy": "latest_observation_then_recent_human_inputs_then_recent_evidence",
        }
        if omitted_count:
            notice: JsonObject = {
                "schema_version": "trace.reasoning-context-omission.v1",
                "omitted_count": omitted_count,
                "notice": (
                    "Earlier or oversized evidence is omitted from this bounded projection, "
                    "not deleted. Do not assume omitted constraints or sources are absent. "
                    "Request the needed scope before claiming preservation or verification."
                ),
            }
            evidence = (*evidence, notice)
        return evidence, selection

    def _pause_for_signal(self, run: AgentRun, *, now: datetime) -> AgentRun | None:
        """Observe admitted steering without replacing an in-flight runtime decision.

        The callback must authenticate its channel input and read its durable inbox
        without waiting for the Run lock. This method runs under that lock at a
        boundary before planning or before a fresh invocation is admitted.
        """
        if self.boundary_signal is None:
            return None
        signal = self.boundary_signal(run.tenant_id, run.run_id)
        if signal is None:
            return None
        event_id, actor_id, note = (signal.get(key) for key in ("event_id", "actor_id", "note"))
        if (
            not isinstance(event_id, str)
            or not 0 < len(event_id) <= _MAX_STEERING_EVENT_ID
            or not isinstance(actor_id, str)
            or not 0 < len(actor_id) <= _MAX_STEERING_ACTOR_ID
            or not isinstance(note, str)
            or not note.strip()
            or len(note) > _MAX_STEERING_NOTE
        ):
            raise ValueError("work_boundary_signal_invalid")
        payload: JsonObject = {
            "schema_version": "trace.work-interruption.v1",
            "event_id": event_id,
            "actor_id": actor_id,
            "note": note,
            "verification": "human_reported",
            "authority": "task_input_only",
        }
        record_id = "interruption-" + contract_sha256({"run_id": run.run_id, "event_id": event_id})
        digest = contract_sha256(payload)
        existing = next(
            (
                item
                for item in self.repository.records(run.tenant_id, run.run_id)
                if item.record_id == record_id
            ),
            None,
        )
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise ValueError("work_boundary_signal_idempotency_conflict")
            return None
        session = self.runtime_store.load(run.run_id)
        if session is not None and session.pending_invocation is not None:
            # Once admitted, the runtime must finish or reconcile its existing call.
            # A channel signal cannot clear that write-ahead execution ownership.
            return None
        return self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.OBSERVE,
                input_sha256=digest,
                output_sha256=digest,
                now=now,
            ),
            state=AgentRunState.AWAITING_INPUT,
            expected_revision=run.revision,
            records=(
                _record(
                    run,
                    record_id=record_id,
                    kind=AgentRecordKind.EVIDENCE,
                    payload=payload,
                    now=now,
                ),
            ),
        )

    def _await_knowledge_input(
        self,
        run: AgentRun,
        preparation: RequiredContextPreparationError | BrandUnresolvedPreparation,
        snapshot: CapabilitySnapshot,
        *,
        now: datetime,
    ) -> AgentRun:
        payload: JsonObject = {
            "schema_version": "trace.knowledge-preparation-blocked.v1",
            "preparation": preparation.model_dump(mode="json"),
        }
        snapshot_already_recorded = any(
            record.record_id == snapshot.snapshot_id
            for record in self.repository.records(run.tenant_id, run.run_id)
        )
        snapshot_records = (
            ()
            if snapshot_already_recorded
            else (
                _record(
                    run,
                    record_id=snapshot.snapshot_id,
                    kind=AgentRecordKind.CAPABILITY_SNAPSHOT,
                    payload=snapshot.model_dump(mode="json"),
                    now=now,
                ),
            )
        )
        return self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.OBSERVE,
                input_sha256=contract_sha256(run.goal),
                output_sha256=contract_sha256(payload),
                now=now,
            ),
            state=AgentRunState.AWAITING_INPUT,
            expected_revision=run.revision,
            records=(
                _record(
                    run,
                    record_id=f"{run.run_id}:knowledge-blocked:{run.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload=payload,
                    now=now,
                ),
                *snapshot_records,
            ),
            blocked_reason=preparation.status,
        )

    def _execute_tool(  # noqa: PLR0913 - explicit immutable bindings define the admission edge.
        self,
        run: AgentRun,
        *,
        intent: AgentIntent,
        capability_id: str,
        tool_input: JsonObject,
        snapshot: CapabilitySnapshot,
        now: datetime,
    ) -> AgentRun:
        descriptor = next(
            (item for item in snapshot.descriptors if item.capability_id == capability_id),
            None,
        )
        if descriptor is None:
            raise ValueError("reasoning_selected_tool_outside_snapshot")
        _validate_json_schema(tool_input, descriptor.input_schema, "tool_input_schema_invalid")
        input_sha256 = contract_sha256(tool_input)
        invocation = ToolInvocation(
            tenant_id=run.tenant_id,
            schema_version="trace.tool-invocation.v1",
            invocation_id=f"{run.run_id}:invocation:{run.revision}",
            run_id=run.run_id,
            step_id=f"{run.run_id}:step:{run.revision}",
            intent_sha256=contract_sha256(intent),
            capability_snapshot_sha256=snapshot.digest,
            descriptor_sha256=contract_sha256(descriptor),
            idempotency_key=_idempotency_key(run, descriptor, input_sha256),
            input=tool_input,
            input_sha256=input_sha256,
        )
        if descriptor.approval_policy.mode == "required":
            return self._append_step(
                run,
                AgentStep(
                    schema_version="trace.agent-step.v1",
                    step_id=f"{run.run_id}:step:{run.revision}",
                    run_id=run.run_id,
                    sequence=run.revision,
                    kind=AgentStepKind.APPROVE,
                    state="awaiting_approval",
                    input_sha256=contract_sha256(intent),
                    parent_step_sha256=run.head_step_sha256,
                    occurred_at=now,
                ),
                state=AgentRunState.AWAITING_APPROVAL,
                expected_revision=run.revision,
                records=(
                    _record(
                        run,
                        record_id=invocation.invocation_id,
                        kind=AgentRecordKind.INVOCATION,
                        payload=invocation.model_dump(mode="json"),
                        now=now,
                    ),
                ),
            )
        return self._dispatch_tool(
            run,
            invocation=invocation,
            descriptor=descriptor,
            approval=None,
            now=now,
            persist_invocation=True,
        )

    def _dispatch_tool(  # noqa: PLR0911,PLR0912,PLR0913,C901 - keep steering and execution admission guards explicit.
        self,
        run: AgentRun,
        *,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
        approval: ToolApproval | None,
        now: datetime,
        persist_invocation: bool,
        admitted_already: bool = False,
    ) -> AgentRun:
        checkpoint(
            {
                "research.search": "자료를 검색하고 있습니다",
                "research.web": "조사 자료를 확인하고 있습니다",
                "github.issue.create": "GitHub 이슈를 등록하고 확인하고 있습니다",
                "skills.list": "사용 가능한 스킬을 조회하고 있습니다",
                "skill_list": "사용 가능한 스킬을 조회하고 있습니다",
                "skills.read": "요청에 맞는 스킬을 읽고 있습니다",
                "skill_get": "요청에 맞는 스킬을 읽고 있습니다",
                "skill_apply": "요청한 공용 스킬을 저장하고 있습니다",
                "creative.image.generate": "이미지를 생성하고 있습니다",
                "creative.trace_post": "이미지 제작을 준비하고 있습니다",
                "creative.image.edit": "이미지 편집을 준비하고 있습니다",
            }.get(descriptor.capability_id, "요청한 도구 작업을 실행하고 있습니다")
        )
        interrupted = self._pause_for_signal(run, now=now)
        if interrupted is not None:
            return interrupted
        _ = self.registry.require_current_dispatch(
            descriptor, policy=self.capability_policy, now=now
        )
        if not self.knowledge_is_current(run.tenant_id, run.run_id):
            session = self.runtime_store.load(run.run_id)
            if session is not None and session.pending_invocation is not None:
                # Current runtime admission cannot be superseded by a new plan.
                # Preserve its reservation and exact call for explicit reconciliation;
                # no provider effect or invented schema-breaking receipt is produced.
                return self._mark_reconciliation(run, contract_sha256(invocation), now=now)
            task = project_task(run, self.repository.records(run.tenant_id, run.run_id))
            task = TaskProjection(
                task.spec, task.checkpoint.model_copy(update={"next_action": "plan"})
            )
            return self._save_task(run, task, now=now)
        adapter = self.tools.get(descriptor.capability_id)
        if adapter is None:
            raise ValueError("tool_dispatch_adapter_unavailable")
        invocation_sha256 = contract_sha256(invocation)
        try:
            self.repository.claim_tool_idempotency(
                tenant_id=run.tenant_id,
                run_id=run.run_id,
                idempotency_key=invocation.idempotency_key,
                invocation_sha256=invocation_sha256,
                claimed_at=now.isoformat(),
            )
        except AgentRunConflictError:
            return self._append_step(
                run,
                AgentStep(
                    schema_version="trace.agent-step.v1",
                    step_id=f"{run.run_id}:step:{run.revision}",
                    run_id=run.run_id,
                    sequence=run.revision,
                    kind=AgentStepKind.EXECUTE,
                    state="failed",
                    input_sha256=invocation_sha256,
                    parent_step_sha256=run.head_step_sha256,
                    occurred_at=now,
                ),
                state=AgentRunState.BLOCKED,
                expected_revision=run.revision,
                blocked_reason="tool_idempotency_conflict",
            )
        admitted = run
        if not admitted_already:
            admitted = self._append_step(
                run,
                _step(
                    run,
                    kind=AgentStepKind.EXECUTE,
                    input_sha256=invocation.intent_sha256,
                    output_sha256=invocation_sha256,
                    now=now,
                ),
                state=AgentRunState.RUNNING,
                expected_revision=run.revision,
                records=(
                    (
                        _record(
                            run,
                            record_id=invocation.invocation_id,
                            kind=AgentRecordKind.INVOCATION,
                            payload=invocation.model_dump(mode="json"),
                            now=now,
                        ),
                    )
                    if persist_invocation
                    else ()
                ),
            )
            self._fault("execute_committed")
        runtime_capability = ToolCapability(
            descriptor.capability_id,
            contract_sha256(descriptor),
            contract_sha256(descriptor.input_schema),
            descriptor.effect_class,
            descriptor.cost.worst_case_units,
            approval_required=_runtime_approval_override(descriptor),
        )
        bound = bind_tool_invocation(
            runtime_capability,
            call_id=invocation.invocation_id,
            idempotency_key=invocation.idempotency_key,
            request=invocation.input,
        )
        session = self.runtime_store.load(run.run_id) or AgentSession(
            run.run_id,
            Budget(run.budget.max_tool_calls, run.budget.max_cost_units),
        )
        grant = None
        if approval is not None:
            if approval.expires_at is None:
                raise ValueError("approval_expiry_required")
            grant = ApprovalGrant(
                grant_id=approval.approval_id,
                call_sha256=bound.call.digest,
                approver_id=approval.approver_id,
                expires_at=approval.expires_at,
            )
        if bound.call.idempotency_key in session.dispatched_idempotency_keys:
            if (
                session.pending_invocation != bound
                or session.state is RuntimeState.AWAITING_RECONCILIATION
                or session.execution_started
            ):
                if (
                    session.pending_invocation == bound
                    and session.state is RuntimeState.EXECUTING
                    and session.execution_started
                ):
                    _ = self.runtime.reconcile_interrupted_execution(
                        self.runtime_store, session, now=now
                    )
                return self._mark_reconciliation(admitted, invocation_sha256, now=now)
            dispatched = session
        else:
            dispatched = self.runtime.request_persisted_tool(
                self.runtime_store,
                session,
                ToolAdmission(runtime_capability, bound, grant),
                now=now,
            )
            self._fault("runtime_admitted")
        backend = _AdapterBackend(
            adapter,
            invocation,
            descriptor,
            None if grant is None else grant.digest,
            on_deferred=lambda deferred: self._persist_deferred_ack(admitted, deferred, now=now),
        )
        started = self.runtime.start_persisted_tool_execution(
            self.runtime_store, dispatched, now=now
        )
        self._fault("execution_started")
        completed = self.runtime.finish_persisted_tool_execution(
            self.runtime_store, started, backend, now=now
        )
        self._fault("runtime_result_persisted")
        if backend.deferred is not None:
            return self._await_deferred(
                self._required_run(run.tenant_id, run.run_id), invocation, backend.deferred, now=now
            )
        if completed.state.value == "awaiting_reconciliation" or backend.result is None:
            return self._append_step(
                admitted,
                AgentStep(
                    schema_version="trace.agent-step.v1",
                    step_id=f"{admitted.run_id}:step:{admitted.revision}",
                    run_id=admitted.run_id,
                    sequence=admitted.revision,
                    kind=AgentStepKind.VERIFY,
                    state="failed",
                    input_sha256=invocation_sha256,
                    parent_step_sha256=admitted.head_step_sha256,
                    occurred_at=now,
                ),
                state=AgentRunState.AWAITING_RECONCILIATION,
                expected_revision=admitted.revision,
            )
        return self._record_tool_result(
            admitted, invocation, descriptor, approval, backend.result, now=now
        )

    def _record_tool_result(  # noqa: PLR0913 - exact terminal result bindings.
        self,
        admitted: AgentRun,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
        approval: ToolApproval | None,
        result: ToolExecutionResult,
        *,
        now: datetime,
    ) -> AgentRun:
        run = admitted
        invocation_sha256 = contract_sha256(invocation)
        receipt = ToolReceiptRecord(
            schema_version="trace.tool-receipt.v1",
            receipt_id=f"{run.run_id}:receipt:{admitted.revision}",
            invocation_sha256=invocation_sha256,
            approval_sha256=None if approval is None else contract_sha256(approval),
            disposition=result.disposition,
            actual_cost_units=result.actual_cost_units,
            output_schema_sha256=contract_sha256(descriptor.output_schema),
            output_sha256=contract_sha256(result.output),
            executor_id=result.executor_id,
            occurred_at=now,
        )
        receipt_sha256 = contract_sha256(receipt)
        evidence_payload: JsonObject = {
            "schema_version": "trace.tool-output-evidence.v1",
            "capability_id": descriptor.capability_id,
            "receipt_sha256": receipt_sha256,
            "output": result.output,
        }
        admission = None
        after_commit = None
        if self.knowledge is not None and self.knowledge.terminal is not None:
            source = self.knowledge.ingress.source_for_run(run.run_id)
            if source is not None:
                admission = self.knowledge.terminal.for_receipt(
                    source,
                    receipt,
                    capability_id=descriptor.capability_id,
                )
                after_commit = self.knowledge.terminal.after_commit
        task = project_task(run, self.repository.records(run.tenant_id, run.run_id))
        queue_admission = (
            None if self.drive_admission is None else self.drive_admission(run, task, now)
        )

        def admit_result(connection: Connection) -> None:
            if admission is not None:
                admission(connection)
            if queue_admission is not None:
                queue_admission(connection)

        verified = self._append_step(
            admitted,
            _step(
                admitted,
                kind=AgentStepKind.VERIFY,
                input_sha256=invocation_sha256,
                output_sha256=receipt_sha256,
                now=now,
            ),
            state=AgentRunState.RUNNING,
            expected_revision=admitted.revision,
            records=(
                _record(
                    admitted,
                    record_id=receipt.receipt_id,
                    kind=AgentRecordKind.RECEIPT,
                    payload=receipt.model_dump(mode="json"),
                    now=now,
                ),
                _record(
                    admitted,
                    record_id=f"{run.run_id}:evidence:{admitted.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload=evidence_payload,
                    now=now,
                ),
            ),
            admission=admit_result,
            after_commit=after_commit,
        )
        self._fault("verify_committed")
        return verified

    def _evaluate_tool_result(
        self, run: AgentRun, receipt: ToolReceiptRecord, evidence: JsonObject, *, now: datetime
    ) -> AgentRun:
        paused = self._deferred_pause_requested(run, receipt.invocation_sha256)
        task = project_task(run, self.repository.records(run.tenant_id, run.run_id))
        invocation = self._latest_invocation(run.tenant_id, run.run_id)
        capability_id = self._descriptor_for_invocation(
            run.tenant_id, run.run_id, invocation
        ).capability_id
        failed_fingerprint = (
            stable_failure_fingerprint(capability_id, evidence)
            if receipt.disposition == "failed"
            else None
        )
        progress = observe_progress(
            task.checkpoint,
            ProgressObservation(
                outcome_fingerprint=failed_fingerprint
                or outcome_fingerprint(
                    self._descriptor_for_invocation(
                        run.tenant_id, run.run_id, invocation
                    ).capability_id,
                    invocation.input,
                    evidence,
                ),
                evidence_sha256s=(progress_evidence_fingerprint(evidence),)
                if receipt.disposition == "succeeded"
                else (),
                satisfied_obligation_ids=(),
                failure_description=f"{capability_id} returned the same failure code"
                if failed_fingerprint
                else None,
            ),
        )
        task = TaskProjection(
            task.spec,
            progress.model_copy(
                update={
                    "next_action": "wait"
                    if paused or progress.disposition == "blocked"
                    else "plan",
                }
            ),
        )
        return self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.EVALUATE,
                input_sha256=contract_sha256(receipt),
                output_sha256=contract_sha256(evidence),
                now=now,
            ),
            state=AgentRunState.BLOCKED
            if progress.disposition == "blocked"
            else AgentRunState.AWAITING_INPUT
            if paused
            else AgentRunState.RUNNING,
            expected_revision=run.revision,
            records=task_records(run, task, now),
            blocked_reason=progress.wait_reason if progress.disposition == "blocked" else None,
        )

    def _deferred_pause_requested(self, run: AgentRun, invocation_sha256: str) -> bool:
        after_invocation = False
        action = None
        for record in self.repository.records(run.tenant_id, run.run_id):
            if record.kind is AgentRecordKind.INVOCATION:
                after_invocation = record.payload_sha256 == invocation_sha256
            elif after_invocation and record.payload.get("deferred_input") is True:
                action = record.payload.get("action")
        return action == "pause"

    def _deferred_for_invocation(
        self, run: AgentRun, invocation: ToolInvocation
    ) -> ToolExecutionDeferred | None:
        for record in self.repository.records(run.tenant_id, run.run_id):
            if record.payload_schema_version == "trace.tool-deferred.v1":
                deferred = ToolExecutionDeferred.model_validate(record.payload)
                if deferred.invocation_sha256 == contract_sha256(invocation):
                    return deferred
        return None

    def _deferred_completion(self, run: AgentRun, operation_id: str) -> AgentRecord | None:
        return next(
            (
                record
                for record in self.repository.records(run.tenant_id, run.run_id)
                if record.payload_schema_version == "trace.tool-completion.v1"
                and record.payload.get("operation_id") == operation_id
            ),
            None,
        )

    def _persist_deferred_ack(
        self, run: AgentRun, deferred: ToolExecutionDeferred, *, now: datetime
    ) -> None:
        run = self._required_run(run.tenant_id, run.run_id)
        for record in self.repository.records(run.tenant_id, run.run_id):
            if record.payload_schema_version == "trace.tool-deferred.v1":
                previous = ToolExecutionDeferred.model_validate(record.payload)
                if previous.operation_id == deferred.operation_id:
                    if previous != deferred:
                        raise ValueError("deferred_operation_conflict")
                    return
        _ = self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.EXECUTE,
                input_sha256=deferred.invocation_sha256,
                output_sha256=contract_sha256(deferred),
                now=now,
            ),
            state=AgentRunState.RUNNING,
            expected_revision=run.revision,
            records=(
                _record(
                    run,
                    record_id=f"{run.run_id}:deferred:{run.revision}",
                    kind=AgentRecordKind.EVIDENCE,
                    payload=deferred.model_dump(mode="json"),
                    now=now,
                ),
            ),
        )
        self._fault("deferred_ack_committed")

    def _await_deferred(
        self,
        run: AgentRun,
        invocation: ToolInvocation,
        deferred: ToolExecutionDeferred,
        *,
        now: datetime,
    ) -> AgentRun:
        session = self.runtime_store.load(run.run_id)
        if (
            session is None
            or session.pending_call is None
            or session.pending_call.call_id != invocation.invocation_id
            or deferred.invocation_sha256 != contract_sha256(invocation)
        ):
            raise ValueError("deferred_runtime_binding_missing")
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            if run.state is AgentRunState.AWAITING_RECONCILIATION:
                return run
            return self._mark_reconciliation(run, deferred.invocation_sha256, now=now)
        acknowledgement = pending_deferred_execution(session)
        if acknowledgement is None:
            # The canonical acknowledgement was saved before the runtime acknowledgement.
            # Finish only that local acknowledgement; never enter the original adapter again.
            session = self.runtime.finish_persisted_tool_execution(
                self.runtime_store, session, _AcknowledgedBackend(deferred), now=now
            )
            acknowledgement = pending_deferred_execution(session)
        if (
            acknowledgement is None
            or acknowledgement.operation_id != deferred.operation_id
            or deferred.invocation_sha256 != contract_sha256(invocation)
        ):
            raise ValueError("deferred_runtime_acknowledgement_invalid")
        if run.state is AgentRunState.AWAITING_TOOL:
            return run
        waiting = self._append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.EXECUTE,
                input_sha256=deferred.invocation_sha256,
                output_sha256=contract_sha256(deferred),
                now=now,
            ),
            state=AgentRunState.AWAITING_TOOL,
            expected_revision=run.revision,
        )
        self._fault("deferred_wait_committed")
        return waiting

    def mark_deferred_uncertain(
        self,
        tenant_id: str,
        run_id: str,
        *,
        operation_id: str,
        now: datetime,
        failure_code: str | None = None,
    ) -> AgentRun:
        """Internal worker readback boundary; elapsed time alone is not evidence of uncertainty."""
        with self.run_locks.hold(tenant_id, run_id):
            run = self._required_run(tenant_id, run_id)
            invocation = self._execution_invocation(tenant_id, run_id)
            deferred = self._deferred_for_invocation(run, invocation)
            if deferred is None or deferred.operation_id != operation_id:
                raise ValueError("deferred_operation_unknown")
            run = self._await_deferred(run, invocation, deferred, now=now)
            session = self.runtime_store.load(run_id)
            if session is None:
                raise ValueError("deferred_runtime_binding_missing")
            if session.state is not RuntimeState.AWAITING_RECONCILIATION:
                _ = self.runtime.mark_persisted_deferred_uncertain(
                    self.runtime_store, session, operation_id, now=now
                )
                self._fault("deferred_uncertainty_persisted")
            run = self._await_deferred(run, invocation, deferred, now=now)
            if failure_code is not None:
                if failure_code not in FAILURE_TEXT:
                    code = "deferred_failure_code_invalid"
                    raise ValueError(code)
                record_id = f"{run_id}:provider-failure:{operation_id}"
                if not any(
                    r.record_id == record_id for r in self.repository.records(tenant_id, run_id)
                ):
                    payload: JsonObject = {
                        "schema_version": "trace.deferred-provider-failure.v1",
                        "operation_id": operation_id,
                        "reason_code": failure_code,
                    }
                    run = self._append_step(
                        run,
                        _step(
                            run,
                            kind=AgentStepKind.VERIFY,
                            input_sha256=contract_sha256(invocation),
                            output_sha256=contract_sha256(payload),
                            now=now,
                        ),
                        state=run.state,
                        expected_revision=run.revision,
                        records=(
                            _record(
                                run,
                                record_id=record_id,
                                kind=AgentRecordKind.EVIDENCE,
                                payload=payload,
                                now=now,
                            ),
                        ),
                    )
            return run

    def complete_deferred(  # noqa: C901,PLR0912 - exact completion and crash recovery guards.
        self,
        tenant_id: str,
        run_id: str,
        *,
        operation_id: str,
        result: ToolExecutionResult,
        now: datetime,
    ) -> AgentRun:
        """Internal owner API. Worker authentication and artifact verification precede this call."""
        with self.run_locks.hold(tenant_id, run_id):
            run = self._required_run(tenant_id, run_id)
            result = ToolExecutionResult.model_validate_json(result.model_dump_json())
            records = self.repository.records(tenant_id, run_id)
            deferred = next(
                (
                    ToolExecutionDeferred.model_validate(record.payload)
                    for record in records
                    if record.payload_schema_version == "trace.tool-deferred.v1"
                    and record.payload.get("operation_id") == operation_id
                ),
                None,
            )
            if deferred is None:
                raise ValueError("deferred_operation_unknown")
            if (
                result.invocation_sha256 != deferred.invocation_sha256
                or result.executor_id != deferred.executor_id
            ):
                raise ValueError("deferred_result_binding_invalid")
            invocation = next(
                (
                    ToolInvocation.model_validate(record.payload)
                    for record in records
                    if record.kind is AgentRecordKind.INVOCATION
                    and record.payload_sha256 == deferred.invocation_sha256
                ),
                None,
            )
            if invocation is None:
                raise ValueError("deferred_invocation_missing")
            descriptor = self._descriptor_for_invocation(tenant_id, run_id, invocation)
            _validate_terminal_result(result, invocation, descriptor)
            completion = self._deferred_completion(run, operation_id)
            if completion is not None and completion.payload.get("result") != result.model_dump(
                mode="json"
            ):
                raise ValueError("deferred_completion_conflict")
            session = self.runtime_store.load(run_id)
            if session is None:
                raise ValueError("deferred_runtime_binding_missing")
            bound = _runtime_bound(invocation, descriptor)
            approval, grant_digest = self._admitted_deferred_approval(
                run, session, bound, invocation
            )
            receipt = ToolReceipt(
                call_id=bound.call.call_id,
                call_sha256=bound.call.digest,
                approval_grant_sha256=grant_digest,
                disposition=EffectDisposition(result.disposition),
                actual_cost_units=result.actual_cost_units,
                receipt_sha256=contract_sha256(result.output),
            )
            settled = next(
                (
                    tool_receipt_from_event(event)
                    for event in session.events
                    if event.event_type in {"tool_succeeded", "tool_no_effect", "tool_failed"}
                    and event.payload.get("call_id") == bound.call.call_id
                ),
                None,
            )
            if settled is not None and settled != receipt:
                raise ValueError("deferred_runtime_receipt_conflict")
            if completion is None:
                if settled is not None or run.state not in {
                    AgentRunState.AWAITING_TOOL,
                    AgentRunState.RUNNING,
                    AgentRunState.AWAITING_RECONCILIATION,
                }:
                    raise ValueError("deferred_completion_state_invalid")
                run = self._await_deferred(run, invocation, deferred, now=now)
                payload: JsonObject = {
                    "schema_version": "trace.tool-completion.v1",
                    "operation_id": operation_id,
                    "result": result.model_dump(mode="json"),
                }
                run = self._append_step(
                    run,
                    _step(
                        run,
                        kind=AgentStepKind.EXECUTE,
                        input_sha256=deferred.invocation_sha256,
                        output_sha256=contract_sha256(payload),
                        now=now,
                    ),
                    state=run.state,
                    expected_revision=run.revision,
                    records=(
                        _record(
                            run,
                            record_id=f"{run_id}:completion:{run.revision}",
                            kind=AgentRecordKind.EVIDENCE,
                            payload=payload,
                            now=now,
                        ),
                    ),
                )
                self._fault("deferred_completion_committed")
            if settled is None:
                session = self.runtime_store.load(run_id)
                if session is None:
                    raise ValueError("deferred_runtime_binding_missing")
                if session.state is RuntimeState.AWAITING_RECONCILIATION:
                    _ = self.runtime.resolve_persisted_reconciliation(
                        self.runtime_store, session, receipt, now=now
                    )
                else:
                    _ = self.runtime.resolve_persisted_deferred(
                        self.runtime_store, session, operation_id, receipt, now=now
                    )
                self._fault("deferred_runtime_settled")
            if any(
                record.kind is AgentRecordKind.RECEIPT
                and record.payload.get("invocation_sha256") == deferred.invocation_sha256
                for record in self.repository.records(tenant_id, run_id)
            ):
                if (
                    run.state is AgentRunState.RUNNING
                    and self._execution_invocation(tenant_id, run_id) == invocation
                ):
                    return run
                return run
            return self._record_tool_result(run, invocation, descriptor, approval, result, now=now)

    def _admitted_deferred_approval(  # noqa: C901 - original dispatch and approval bindings.
        self,
        run: AgentRun,
        session: AgentSession,
        bound: BoundToolInvocation,
        invocation: ToolInvocation,
    ) -> tuple[ToolApproval | None, str | None]:
        dispatch = None
        for event in session.events:
            value = event.payload.get("invocation")
            if event.event_type != "tool_dispatched" or not isinstance(value, dict):
                continue
            call = value.get("call")
            if call == tool_call_payload(bound.call) and value.get("request") == bound.request:
                dispatch = event
                break
        if dispatch is None:
            raise ValueError("deferred_runtime_admission_missing")
        grant_digest = dispatch.payload.get("approval_grant_sha256")
        if grant_digest is None:
            return None, None
        if not isinstance(grant_digest, str):
            raise ValueError("deferred_runtime_grant_invalid")
        for record in self.repository.records(run.tenant_id, run.run_id):
            if record.kind is AgentRecordKind.APPROVAL:
                approval = ToolApproval.model_validate(record.payload)
                if (
                    approval.invocation_sha256 != contract_sha256(invocation)
                    or approval.decision != "granted"
                    or approval.expires_at is None
                ):
                    continue
                grant = ApprovalGrant(
                    approval.approval_id,
                    bound.call.digest,
                    approval.approver_id,
                    approval.expires_at,
                )
                if grant.digest == grant_digest:
                    return approval, grant_digest
        raise ValueError("deferred_original_approval_missing")

    @staticmethod
    def _validate_reasoning_decision(
        snapshot: CapabilitySnapshot, decision: ReasoningDecision | ReasoningDecisionV2
    ) -> None:
        if decision.action != "invoke_tool":
            return
        descriptor = next(
            (item for item in snapshot.descriptors if item.capability_id == decision.capability_id),
            None,
        )
        if descriptor is None or decision.tool_input is None:
            raise ValueError("reasoning_selected_tool_outside_snapshot")
        _validate_json_schema(
            decision.tool_input, descriptor.input_schema, "tool_input_schema_invalid"
        )

    def _mark_reconciliation(
        self, run: AgentRun, invocation_sha256: str, *, now: datetime
    ) -> AgentRun:
        return self._append_step(
            run,
            AgentStep(
                schema_version="trace.agent-step.v1",
                step_id=f"{run.run_id}:step:{run.revision}",
                run_id=run.run_id,
                sequence=run.revision,
                kind=AgentStepKind.VERIFY,
                state="failed",
                input_sha256=invocation_sha256,
                parent_step_sha256=run.head_step_sha256,
                occurred_at=now,
            ),
            state=AgentRunState.AWAITING_RECONCILIATION,
            expected_revision=run.revision,
        )

    def _latest_invocation(self, tenant_id: str, run_id: str) -> ToolInvocation:
        records = self.repository.records(tenant_id, run_id)
        for record in reversed(records):
            if record.kind is AgentRecordKind.INVOCATION:
                return ToolInvocation.model_validate(record.payload)
        raise ValueError("pending_tool_invocation_missing")

    def _execution_invocation(self, tenant_id: str, run_id: str) -> ToolInvocation:
        """Recover the persisted execution target, not the last planned/read invocation."""
        invocations = {
            contract_sha256(r.payload): ToolInvocation.model_validate(r.payload)
            for r in self.repository.records(tenant_id, run_id)
            if r.kind is AgentRecordKind.INVOCATION
        }
        for step in reversed(self.repository.steps(tenant_id, run_id)):
            if step.kind is AgentStepKind.EXECUTE:
                # Dispatch binds output; deferred waits bind input to the invocation.
                for digest in (step.output_sha256, step.input_sha256):
                    if digest is not None and digest in invocations:
                        return invocations[digest]
        message = "execution_tool_invocation_missing"
        raise ValueError(message)

    def pending_approval(self, tenant_id: str, run_id: str) -> ToolInvocation | None:
        """Return the current proposal, even after intervening read-only tool calls."""
        return pending_approval(self.repository.records(tenant_id, run_id))

    def knowledge_is_current(self, tenant_id: str, run_id: str) -> bool:
        """Recheck canonical knowledge authority immediately before an optional worker effect."""
        if self.repository.get(tenant_id, run_id) is None:
            return False
        if self.knowledge is None:
            return True
        try:
            prepared = self._latest_prepared_context(tenant_id, run_id)
            return prepared is not None and self.knowledge.is_current(run_id, prepared)
        except Exception:  # noqa: BLE001 - failed authority/readback never grants a worker effect.
            return False

    def _latest_prepared_context(
        self,
        tenant_id: str,
        run_id: str,
    ) -> PreparedKnowledgeContext | None:
        for record in reversed(self.repository.records(tenant_id, run_id)):
            if record.kind is not AgentRecordKind.EVIDENCE:
                continue
            if record.payload_schema_version != "trace.prepared-knowledge-context-record.v1":
                continue
            payload = record.payload.get("prepared_context")
            if isinstance(payload, dict):
                return PreparedKnowledgeContext.model_validate(payload)
        return None

    def _descriptor_for_invocation(
        self, tenant_id: str, run_id: str, invocation: ToolInvocation
    ) -> ToolDescriptor:
        for record in reversed(self.repository.records(tenant_id, run_id)):
            if record.kind is not AgentRecordKind.CAPABILITY_SNAPSHOT:
                continue
            snapshot = CapabilitySnapshot.model_validate(record.payload)
            if snapshot.digest != invocation.capability_snapshot_sha256:
                continue
            descriptor = next(
                (
                    item
                    for item in snapshot.descriptors
                    if contract_sha256(item) == invocation.descriptor_sha256
                ),
                None,
            )
            if descriptor is not None:
                return descriptor
        raise ValueError("invocation_frozen_descriptor_missing")

    def _approval_for_invocation(
        self,
        tenant_id: str,
        run_id: str,
        invocation: ToolInvocation,
        *,
        now: datetime,
    ) -> ToolApproval | None:
        descriptor = self._descriptor_for_invocation(tenant_id, run_id, invocation)
        if descriptor.approval_policy.mode == "none":
            return None
        invocation_sha256 = contract_sha256(invocation)
        for record in reversed(self.repository.records(tenant_id, run_id)):
            if record.kind is not AgentRecordKind.APPROVAL:
                continue
            approval = ToolApproval.model_validate(record.payload)
            if approval.invocation_sha256 != invocation_sha256:
                continue
            if (
                approval.decision != "granted"
                or approval.expires_at is None
                or approval.expires_at < now
            ):
                raise ValueError("tool_approval_not_dispatchable")
            return approval
        raise ValueError("tool_approval_missing")

    def _spent_cost(self, tenant_id: str, run_id: str) -> int:
        return sum(
            _receipt_cost(record.payload)
            for record in self.repository.records(tenant_id, run_id)
            if record.kind is AgentRecordKind.RECEIPT
        )

    def _tool_calls(self, tenant_id: str, run_id: str) -> int:
        return sum(
            record.kind is AgentRecordKind.INVOCATION
            for record in self.repository.records(tenant_id, run_id)
        )

    def _required_run(self, tenant_id: str, run_id: str) -> AgentRun:
        run = self.repository.get(tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        return run

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)


def _step(
    run: AgentRun,
    *,
    kind: AgentStepKind,
    input_sha256: str,
    output_sha256: str,
    now: datetime,
) -> AgentStep:
    return AgentStep(
        schema_version="trace.agent-step.v1",
        step_id=f"{run.run_id}:step:{run.revision}",
        run_id=run.run_id,
        sequence=run.revision,
        kind=kind,
        state="completed",
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        parent_step_sha256=run.head_step_sha256,
        occurred_at=now,
    )


def _record(
    run: AgentRun,
    *,
    record_id: str,
    kind: AgentRecordKind,
    payload: JsonObject,
    now: datetime,
) -> AgentRecord:
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise ValueError("agent_record_payload_schema_missing")
    return AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id=record_id,
        run_id=run.run_id,
        kind=kind,
        payload_schema_version=schema_version,
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=now,
    )


def _receipt_cost(payload: JsonObject) -> int:
    value = payload.get("actual_cost_units")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("tool_receipt_cost_invalid")
    return value


def _idempotency_key(run: AgentRun, descriptor: ToolDescriptor, input_sha256: str) -> str:
    match descriptor.idempotency.key_scope:
        case "run_tool_input":
            scope = run.run_id
        case "tenant_tool_input":
            scope = run.tenant_id
        case _:
            raise ValueError("adapter_defined_idempotency_not_supported")
    return f"{scope}:{descriptor.capability_id}:{input_sha256}"


def _validate_json_schema(instance: object, schema: JsonObject, error_code: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(instance)  # pyright: ignore[reportUnknownMemberType]
    except (SchemaError, JsonSchemaValidationError) as error:
        raise ValueError(error_code) from error


__all__ = ["CreateAgentRunRequest", "MarketingAgentService"]


class _AdapterBackend:
    def __init__(
        self,
        adapter: ToolAdapter,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
        approval_grant_sha256: str | None,
        on_deferred: Callable[[ToolExecutionDeferred], None] | None = None,
    ) -> None:
        self.adapter: ToolAdapter = adapter
        self.invocation: ToolInvocation = invocation
        self.descriptor: ToolDescriptor = descriptor
        self.approval_grant_sha256: str | None = approval_grant_sha256
        self.result: ToolExecutionResult | None = None
        self.deferred: ToolExecutionDeferred | None = None
        self.on_deferred: Callable[[ToolExecutionDeferred], None] | None = on_deferred

    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt | DeferredToolExecution:
        if invocation.request != self.invocation.input:
            raise ValueError("adapter_invocation_input_mismatch")
        result = self.adapter.execute(self.invocation, self.descriptor)
        if result.invocation_sha256 != contract_sha256(self.invocation):
            raise ValueError("adapter_receipt_invocation_mismatch")
        if isinstance(result, ToolExecutionDeferred):
            if self.on_deferred is None:
                raise ValueError("adapter_deferred_owner_missing")
            self.on_deferred(result)
            self.deferred = result
            return DeferredToolExecution(
                invocation.call.call_id, invocation.call.digest, result.operation_id
            )
        _validate_json_schema(
            result.output,
            self.descriptor.output_schema,
            "tool_output_schema_invalid",
        )
        _validate_json_schema(
            result.model_dump(mode="json"),
            self.descriptor.receipt_schema,
            "tool_receipt_schema_invalid",
        )
        self.result = result
        disposition = EffectDisposition(result.disposition)
        return ToolReceipt(
            call_id=invocation.call.call_id,
            call_sha256=invocation.call.digest,
            approval_grant_sha256=self.approval_grant_sha256,
            disposition=disposition,
            actual_cost_units=result.actual_cost_units,
            receipt_sha256=contract_sha256(result.output),
        )


def _runtime_bound(invocation: ToolInvocation, descriptor: ToolDescriptor) -> BoundToolInvocation:
    capability = ToolCapability(
        descriptor.capability_id,
        contract_sha256(descriptor),
        contract_sha256(descriptor.input_schema),
        descriptor.effect_class,
        descriptor.cost.worst_case_units,
        approval_required=_runtime_approval_override(descriptor),
    )
    return bind_tool_invocation(
        capability,
        call_id=invocation.invocation_id,
        idempotency_key=invocation.idempotency_key,
        request=invocation.input,
    )


def _runtime_approval_override(descriptor: ToolDescriptor) -> bool | None:
    """Project the one validated no-approval effect policy into the durable runtime call."""
    if (
        descriptor.approval_policy.mode == "required"
        or descriptor.effect_class is EffectClass.OBSERVE
    ):
        return None
    if (
        descriptor.effect_class is EffectClass.CONTROL_PLANE_WRITE
        and descriptor.credential_boundary == "adapter_owner"
        and descriptor.approval_policy.authority == AUTHENTICATED_SOURCE_AUTHORITY
        and allows_authenticated_source_approval(
            capability_id=descriptor.capability_id,
            owner=descriptor.owner,
            installation_id=descriptor.installation_id,
        )
    ):
        return False
    raise ValueError(_RUNTIME_SOURCE_AUTHORIZED_DESCRIPTOR_INVALID)


def _validate_terminal_result(
    result: ToolExecutionResult, invocation: ToolInvocation, descriptor: ToolDescriptor
) -> None:
    if result.invocation_sha256 != contract_sha256(invocation):
        raise ValueError("adapter_receipt_invocation_mismatch")
    if result.actual_cost_units > descriptor.cost.worst_case_units:
        raise ValueError("deferred_cost_outside_reserved_budget")
    _validate_json_schema(result.output, descriptor.output_schema, "tool_output_schema_invalid")
    _validate_json_schema(
        result.model_dump(mode="json"), descriptor.receipt_schema, "tool_receipt_schema_invalid"
    )


@dataclass(frozen=True, slots=True)
class _AcknowledgedBackend:
    acknowledgement: ToolExecutionDeferred

    def execute(self, invocation: BoundToolInvocation) -> DeferredToolExecution:
        return DeferredToolExecution(
            invocation.call.call_id, invocation.call.digest, self.acknowledgement.operation_id
        )
