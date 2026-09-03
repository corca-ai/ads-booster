"""Channel-neutral application service for canonical on-premises Agent Runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

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
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.marketing.agent_service.sqlite_repository import (
    AgentRunConflictError,
    SqliteAgentRunRepository,
)
from ads_booster.marketing.runtime import (
    AgentSession,
    ApprovalGrant,
    BoundToolInvocation,
    Budget,
    EffectDisposition,
    MarketingAgentRuntime,
    RuntimeState,
    SqliteSessionStore,
    ToolAdmission,
    ToolCapability,
    ToolReceipt,
    bind_tool_invocation,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.marketing.agent_core.ports import ReasoningProvider, ToolAdapter


class CreateAgentRunRequest(ContractModel):
    run_id: str
    tenant_id: str
    goal: AgentGoal
    budget: AgentBudget


@dataclass(slots=True)
class MarketingAgentService:
    repository: SqliteAgentRunRepository
    registry: ToolRegistry
    reasoning: ReasoningProvider
    tools: Mapping[str, ToolAdapter]
    runtime_store: SqliteSessionStore
    fault_hook: Callable[[str], None] | None = None
    capability_policy: CapabilityPolicy = field(default_factory=CapabilityPolicy)
    runtime: MarketingAgentRuntime = field(default_factory=MarketingAgentRuntime)

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

    def create(self, request: CreateAgentRunRequest, *, now: datetime) -> AgentRun:
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
        )
        return self._plan(run, evidence=(), now=now)

    def drive(self, tenant_id: str, run_id: str, *, now: datetime) -> AgentRun:
        """Continue a recoverable reasoning boundary without repeating a claimed effect."""
        run = self._required_run(tenant_id, run_id)
        if run.state is not AgentRunState.RUNNING and run.state is not AgentRunState.CREATED:
            return run
        steps = self.repository.steps(tenant_id, run_id)
        if steps and steps[-1].kind is AgentStepKind.EXECUTE:
            return self._resume_execution(run, now=now)
        if steps and steps[-1].kind is AgentStepKind.VERIFY:
            return self._resume_verified_tool(run, now=now)
        if steps and steps[-1].kind is AgentStepKind.APPROVE:
            return self._resume_approved_invocation(run, now=now)
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
        reasoning = ReasoningResult.model_validate(reasoning_record.payload)
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
        invocation = self._latest_invocation(run.tenant_id, run.run_id)
        descriptor = self._descriptor_for_invocation(run.tenant_id, run.run_id, invocation)
        session = self.runtime_store.load(run.run_id)
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
            (item for item in reversed(records) if item.kind is AgentRecordKind.EVIDENCE), None
        )
        if receipt_record is None or evidence_record is None:
            raise ValueError("verified_tool_recovery_records_missing")
        receipt = ToolReceiptRecord.model_validate(receipt_record.payload)
        evaluated = self.repository.append_step(
            run,
            _step(
                run,
                kind=AgentStepKind.EVALUATE,
                input_sha256=contract_sha256(receipt),
                output_sha256=contract_sha256(evidence_record.payload),
                now=now,
            ),
            state=AgentRunState.RUNNING,
            expected_revision=run.revision,
        )
        return self._plan(evaluated, evidence=(evidence_record.payload,), now=now)

    def _resume_approved_invocation(self, run: AgentRun, *, now: datetime) -> AgentRun:
        invocation = self._latest_invocation(run.tenant_id, run.run_id)
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
    ) -> AgentRun:
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
        resumed = self.repository.append_step(
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
        )
        return self._plan(resumed, evidence=(evidence_payload,), now=now)

    def decide_approval(  # noqa: PLR0913 - exact approval identity and expiry stay explicit.
        self,
        tenant_id: str,
        run_id: str,
        *,
        approver_id: str,
        granted: bool,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> AgentRun:
        """Resolve one exact pending invocation and dispatch only a valid grant."""
        run = self._required_run(tenant_id, run_id)
        if run.state is not AgentRunState.AWAITING_APPROVAL:
            raise ValueError("agent_run_not_awaiting_approval")
        invocation = self._latest_invocation(tenant_id, run_id)
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
        )
        approval_sha256 = contract_sha256(approval)
        decided = self.repository.append_step(
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
        return self._dispatch_tool(
            decided,
            invocation=invocation,
            descriptor=descriptor,
            approval=approval,
            now=now,
            persist_invocation=False,
        )

    def _plan(
        self,
        run: AgentRun,
        *,
        evidence: tuple[JsonObject, ...],
        now: datetime,
    ) -> AgentRun:
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
        snapshot_record = _record(
            run,
            record_id=snapshot.snapshot_id,
            kind=AgentRecordKind.CAPABILITY_SNAPSHOT,
            payload=snapshot.model_dump(mode="json"),
            now=now,
        )
        observed = self.repository.append_step(
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
            records=(snapshot_record,),
        )
        reasoning_request = ReasoningRequest(
            schema_version="trace.reasoning-request.v1",
            run_id=run.run_id,
            phase="plan" if not evidence else "replan",
            goal=run.goal,
            capability_snapshot=snapshot,
            evidence=evidence,
            remaining_tool_calls=max(
                0, run.budget.max_tool_calls - self._tool_calls(run.tenant_id, run.run_id)
            ),
            remaining_cost_units=max(
                0, run.budget.max_cost_units - self._spent_cost(run.tenant_id, run.run_id)
            ),
        )
        reasoning_result = self.reasoning.plan(reasoning_request)
        if reasoning_result.receipt.request_sha256 != contract_sha256(reasoning_request):
            raise ValueError("reasoning_receipt_request_digest_mismatch")
        decision = reasoning_result.decision
        self._validate_reasoning_decision(snapshot, decision)
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
            "stop": AgentRunState.COMPLETED,
            "request_input": AgentRunState.AWAITING_INPUT,
            "invoke_tool": AgentRunState.RUNNING,
        }[decision.action]
        planned = self.repository.append_step(
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
            records=(intent_record, reasoning_record),
        )
        if decision.action != "invoke_tool":
            return planned
        if decision.capability_id is None or decision.tool_input is None:
            raise ValueError("reasoning_tool_action_payload_missing")
        self._fault("plan_committed")
        return self._execute_tool(
            planned,
            intent=intent,
            capability_id=decision.capability_id,
            tool_input=decision.tool_input,
            snapshot=snapshot,
            now=now,
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
            return self.repository.append_step(
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

    def _dispatch_tool(  # noqa: PLR0913 - every execution authority binding is explicit.
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
        _ = self.registry.require_current_dispatch(
            descriptor, policy=self.capability_policy, now=now
        )
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
            return self.repository.append_step(
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
            admitted = self.repository.append_step(
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
        )
        started = self.runtime.start_persisted_tool_execution(
            self.runtime_store, dispatched, now=now
        )
        self._fault("execution_started")
        completed = self.runtime.finish_persisted_tool_execution(
            self.runtime_store, started, backend, now=now
        )
        self._fault("runtime_result_persisted")
        if completed.state.value == "awaiting_reconciliation" or backend.result is None:
            return self.repository.append_step(
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
        result = backend.result
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
        verified = self.repository.append_step(
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
        )
        self._fault("verify_committed")
        evaluated = self.repository.append_step(
            verified,
            _step(
                verified,
                kind=AgentStepKind.EVALUATE,
                input_sha256=receipt_sha256,
                output_sha256=contract_sha256(evidence_payload),
                now=now,
            ),
            state=AgentRunState.RUNNING,
            expected_revision=verified.revision,
        )
        return self._plan(evaluated, evidence=(evidence_payload,), now=now)

    @staticmethod
    def _validate_reasoning_decision(
        snapshot: CapabilitySnapshot, decision: ReasoningDecision
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
        return self.repository.append_step(
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
    ) -> None:
        self.adapter: ToolAdapter = adapter
        self.invocation: ToolInvocation = invocation
        self.descriptor: ToolDescriptor = descriptor
        self.approval_grant_sha256: str | None = approval_grant_sha256
        self.result: ToolExecutionResult | None = None

    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt:
        if invocation.request != self.invocation.input:
            raise ValueError("adapter_invocation_input_mismatch")
        result = self.adapter.execute(self.invocation, self.descriptor)
        if result.invocation_sha256 != contract_sha256(self.invocation):
            raise ValueError("adapter_receipt_invocation_mismatch")
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
