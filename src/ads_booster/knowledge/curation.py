from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.knowledge.batch_curation import CurationBatchWork
from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.curation_contracts import (
    CurationBatchJobContext,
    CurationDecision,
    CurationDecisionAction,
    CurationLimits,
    CurationMemoryIntent,
    CurationProviderError,
    CurationRequest,
    CurationResult,
    CurationRunStatus,
    CurationToolDefinition,
    SourceDispositionIntent,
)
from ads_booster.knowledge.curation_disposition import (
    DispositionApplyRequest,
    SourceDispositionError,
)
from ads_booster.knowledge.curation_runtime import (
    CancellationSignal,
    CurationDecisionProvider,
    CurationDependencies,
    CurationProgress,
    CurationTerminal,
    finish_result,
    tool_budget_error,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.repository_types import RepositoryConflictError
from ads_booster.knowledge.tool_contracts import (
    KnowledgeToolName,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_BATCH_ROUNDS: Final = 8
_MAX_BATCH_ROUND_SECONDS: Final = 120.0
_MAX_BATCH_TOTAL_SECONDS: Final = 900.0


@dataclass(frozen=True, slots=True)
class _CurationStep:
    request: CurationRequest
    context: TrustedInvocationContext
    progress: CurationProgress
    decision_index: int


@dataclass(frozen=True, slots=True)
class _DispositionConflict:
    error_code: str


@dataclass(frozen=True, slots=True)
class _CurationExecution:
    work: CurationBatchWork
    provider: CurationDecisionProvider
    cancellation: CancellationSignal | None
    started: float


@dataclass(frozen=True, slots=True)
class _CurationAdvance:
    progress: CurationProgress
    terminal: CurationTerminal | None = None


def _tool_advance(
    progress: CurationProgress, outcome: CurationProgress | CurationTerminal
) -> _CurationAdvance:
    match outcome:
        case CurationTerminal():
            return _CurationAdvance(progress, outcome)
        case CurationProgress():
            return _CurationAdvance(outcome)


@dataclass(frozen=True, slots=True)
class _BatchJobState:
    work: CurationBatchWork
    progress: CurationProgress = CurationProgress()


@dataclass(frozen=True, slots=True)
class CurationRunner:
    dependencies: CurationDependencies
    limits: CurationLimits = CurationLimits()

    def run(
        self,
        request: CurationRequest,
        trusted_context: TrustedInvocationContext,
        cancellation: CancellationSignal | None = None,
    ) -> CurationResult:
        return self._run(
            _CurationExecution(
                work=CurationBatchWork(request, trusted_context),
                provider=self.dependencies.provider,
                cancellation=cancellation,
                started=time.monotonic(),
            )
        )

    def run_batch(
        self,
        batch_id: str,
        items: tuple[CurationBatchWork, ...],
        cancellation: CancellationSignal | None = None,
    ) -> tuple[CurationResult, ...]:
        if not items:
            return ()
        started = time.monotonic()
        prepared = tuple(
            CurationBatchWork(self._with_tool_catalog(item.request), item.trusted_context)
            for item in items
        )
        binding_error = self._batch_binding_error(prepared)
        if binding_error is not None:
            return tuple(
                finish_result(
                    item.request,
                    CurationProgress(),
                    CurationTerminal(CurationRunStatus.FAILED, binding_error),
                )
                for item in prepared
            )
        active = {item.request.job_id: _BatchJobState(item) for item in prepared}
        completed: dict[str, CurationResult] = {}
        total_seconds = min(self.limits.total_timeout_seconds, _MAX_BATCH_TOTAL_SECONDS)
        round_count = min(self.limits.max_decisions, _MAX_BATCH_ROUNDS)
        for decision_index in range(1, round_count + 1):
            terminal = self._batch_preflight(cancellation, started, total_seconds)
            if terminal is not None:
                self._finish_active(active, completed, terminal)
                break
            states = tuple(active.values())
            remaining_seconds = total_seconds - (time.monotonic() - started)
            if remaining_seconds <= 0:
                self._finish_active(
                    active,
                    completed,
                    CurationTerminal(
                        CurationRunStatus.BUDGET_EXHAUSTED,
                        "curation_total_timeout",
                    ),
                )
                break
            timeout_seconds = min(
                self.limits.decision_timeout_seconds,
                _MAX_BATCH_ROUND_SECONDS,
                remaining_seconds,
            )
            try:
                batch_decision = self.dependencies.provider.decide_batch(
                    batch_id,
                    tuple(
                        CurationBatchJobContext(
                            request=state.work.request,
                            observations=state.progress.observations,
                        )
                        for state in states
                    ),
                    timeout_seconds=timeout_seconds,
                )
            except CurationProviderError:
                self._finish_active(
                    active,
                    completed,
                    CurationTerminal(
                        CurationRunStatus.PROVIDER_UNAVAILABLE,
                        "knowledge_provider_batch_result_invalid",
                    ),
                )
                break
            expected_job_ids = set(active)
            decisions = {item.job_id: item.decision for item in batch_decision.decisions}
            if (
                batch_decision.batch_id != batch_id
                or len(batch_decision.decisions) != len(active)
                or set(decisions) != expected_job_ids
            ):
                self._finish_active(
                    active,
                    completed,
                    CurationTerminal(
                        CurationRunStatus.PROVIDER_UNAVAILABLE,
                        "knowledge_provider_batch_result_invalid",
                    ),
                )
                break
            for job_id, state in tuple(active.items()):
                terminal = self._batch_preflight(cancellation, started, total_seconds)
                if terminal is not None:
                    self._finish_active(active, completed, terminal)
                    break
                step = _CurationStep(
                    state.work.request,
                    state.work.trusted_context,
                    state.progress,
                    decision_index,
                )
                advance = self._advance(step, decisions[job_id])
                if advance.terminal is None:
                    active[job_id] = _BatchJobState(state.work, advance.progress)
                    continue
                completed[job_id] = finish_result(
                    state.work.request,
                    advance.progress,
                    advance.terminal,
                )
                del active[job_id]
            if not active:
                break
        if active:
            self._finish_active(
                active,
                completed,
                CurationTerminal(
                    CurationRunStatus.BUDGET_EXHAUSTED,
                    "curation_decision_budget_exhausted",
                ),
            )
        return tuple(completed[item.request.job_id] for item in prepared)

    def _run(self, execution: _CurationExecution) -> CurationResult:
        request = execution.work.request
        trusted_context = execution.work.trusted_context
        if trusted_context.job_id != request.job_id:
            return finish_result(
                request,
                CurationProgress(),
                CurationTerminal(CurationRunStatus.FAILED, "curation_job_binding_mismatch"),
            )
        request = self._with_tool_catalog(request)
        progress = CurationProgress()
        for decision_index in range(1, self.limits.max_decisions + 1):
            terminal = self._preflight(progress, execution.cancellation, execution.started)
            if terminal is not None:
                return finish_result(request, progress, terminal)
            try:
                decision = execution.provider.decide(
                    request,
                    progress.observations,
                    timeout_seconds=self.limits.decision_timeout_seconds,
                )
            except CurationProviderError:
                return finish_result(
                    request,
                    progress,
                    CurationTerminal(
                        CurationRunStatus.PROVIDER_UNAVAILABLE,
                        "knowledge_provider_result_invalid",
                    ),
                )
            step = _CurationStep(request, trusted_context, progress, decision_index)
            advance = self._advance(step, decision)
            progress = advance.progress
            if advance.terminal is not None:
                return finish_result(request, progress, advance.terminal)
        return finish_result(
            request,
            progress,
            CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_decision_budget_exhausted",
            ),
        )

    def _with_tool_catalog(self, request: CurationRequest) -> CurationRequest:
        return request.model_copy(
            update={
                "auto_memory_enabled": (
                    self.dependencies.memory is not None
                    and request.authenticated_user_event is not None
                    and request.authenticated_user_event.evidence_ref.scope.kind
                    in (ScopeKind.WORKSPACE, ScopeKind.CHANNEL)
                ),
                "tool_catalog": tuple(
                    CurationToolDefinition(name=name, input_schema=schema)
                    for name, schema in self.dependencies.tool_host.schemas().items()
                ),
            }
        )

    @staticmethod
    def _batch_binding_error(items: tuple[CurationBatchWork, ...]) -> str | None:
        if not items:
            return None
        if len(items) > 20:
            return "curation_batch_item_limit_exceeded"
        job_ids = tuple(item.request.job_id for item in items)
        if len(job_ids) != len(set(job_ids)):
            return "curation_batch_job_duplicate"
        first = next(iter(items))
        first_actor = first.trusted_context.actor
        for item in items:
            actor = item.trusted_context.actor
            if item.trusted_context.job_id != item.request.job_id:
                return "curation_batch_job_binding_mismatch"
            if (
                item.request.policy_version != first.request.policy_version
                or actor.workspace_id != first_actor.workspace_id
                or actor.member_id != first_actor.member_id
                or actor.session_id != first_actor.session_id
                or actor.conversation_scope != first_actor.conversation_scope
                or actor.policy_epoch != first_actor.policy_epoch
                or item.trusted_context.capability_epoch != first.trusted_context.capability_epoch
            ):
                return "curation_batch_scope_policy_mismatch"
        return None

    @staticmethod
    def _batch_preflight(
        cancellation: CancellationSignal | None,
        started: float,
        total_seconds: float,
    ) -> CurationTerminal | None:
        if cancellation is not None and cancellation.cancelled():
            return CurationTerminal(CurationRunStatus.CANCELLED, "curation_cancelled")
        if time.monotonic() - started >= total_seconds:
            return CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_total_timeout",
            )
        return None

    @staticmethod
    def _finish_active(
        active: dict[str, _BatchJobState],
        completed: dict[str, CurationResult],
        terminal: CurationTerminal,
    ) -> None:
        for job_id, state in tuple(active.items()):
            completed[job_id] = finish_result(
                state.work.request,
                state.progress,
                terminal,
            )
            del active[job_id]

    def _advance(
        self,
        step: _CurationStep,
        decision: CurationDecision,
    ) -> _CurationAdvance:
        match decision.action:
            case CurationDecisionAction.TOOL_CALL:
                outcome = self._execute_tool(
                    step,
                    decision.tool_name,
                    decision.tool_arguments_json,
                )
                return _tool_advance(step.progress, outcome)
            case CurationDecisionAction.REMEMBER:
                outcome = self._execute_remember(step, decision.memory_intent)
                return _tool_advance(step.progress, outcome)
            case CurationDecisionAction.QUESTION:
                outcome = self._execute_question(step, decision.question_arguments_json)
                match outcome:
                    case CurationTerminal():
                        return _CurationAdvance(step.progress, outcome)
                    case (CurationProgress() as progress, CurationTerminal() as terminal):
                        return _CurationAdvance(progress, terminal)
            case CurationDecisionAction.FINISH:
                progress = step.progress
                if decision.disposition_intent is not None:
                    outcome = self._apply_disposition(step, decision.disposition_intent)
                    match outcome:
                        case CurationTerminal():
                            return _CurationAdvance(progress, outcome)
                        case _DispositionConflict(error_code=error_code):
                            progress = self._conflict_observation(
                                progress,
                                step.decision_index,
                                error_code,
                            )
                            if progress.conflict_count <= self.limits.max_conflict_redecisions:
                                return _CurationAdvance(progress)
                            return _CurationAdvance(
                                progress,
                                CurationTerminal(
                                    CurationRunStatus.BUDGET_EXHAUSTED,
                                    "curation_conflict_budget_exhausted",
                                ),
                            )
                        case CurationProgress():
                            progress = outcome
                return _CurationAdvance(
                    progress,
                    CurationTerminal(
                        CurationRunStatus.FINISHED,
                        targets=decision.targets,
                        disposition=None
                        if decision.disposition_intent is None
                        else decision.disposition_intent.disposition,
                    ),
                )

    def _preflight(
        self,
        progress: CurationProgress,
        cancellation: CancellationSignal | None,
        started: float,
    ) -> CurationTerminal | None:
        if cancellation is not None and cancellation.cancelled():
            return CurationTerminal(CurationRunStatus.CANCELLED, "curation_cancelled")
        if time.monotonic() - started >= self.limits.total_timeout_seconds:
            return CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_total_timeout",
            )
        if progress.conflict_count > self.limits.max_conflict_redecisions:
            return CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_conflict_budget_exhausted",
            )
        return None

    def _execute_tool(
        self,
        step: _CurationStep,
        name: KnowledgeToolName | None,
        encoded: str | None,
    ) -> CurationProgress | CurationTerminal:
        if name is None or encoded is None:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_decision_fields_invalid")
        budget_error = tool_budget_error(name, step.progress, self.limits)
        if budget_error is not None:
            return CurationTerminal(CurationRunStatus.BUDGET_EXHAUSTED, budget_error)
        try:
            arguments = _JSON_OBJECT.validate_json(encoded)
        except ValidationError:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_tool_arguments_invalid")
        invocation = step.context.model_copy(
            update={
                "invocation_id": stable_id(
                    "curation-invocation",
                    step.request.job_id,
                    str(step.decision_index),
                )
            }
        )
        result = self.dependencies.tool_host.execute(name.value, arguments, invocation)
        updated = step.progress.record(step.decision_index, name, result)
        if updated.conflict_count > self.limits.max_conflict_redecisions:
            return CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_conflict_budget_exhausted",
            )
        return updated

    def _execute_remember(
        self, step: _CurationStep, intent: CurationMemoryIntent | None
    ) -> CurationProgress | CurationTerminal:
        writer = self.dependencies.memory
        if writer is None or not step.request.auto_memory_enabled:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_memory_unavailable")
        if intent is None:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_memory_intent_missing")
        result = writer.write(step.request, intent, step.context)
        progress = step.progress.record(step.decision_index, KnowledgeToolName.MEMORY_APPLY, result)
        if progress.conflict_count > self.limits.max_conflict_redecisions:
            return CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED, "curation_conflict_budget_exhausted"
            )
        return progress

    def _execute_question(
        self,
        step: _CurationStep,
        encoded: str | None,
    ) -> tuple[CurationProgress, CurationTerminal] | CurationTerminal:
        if encoded is None:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_decision_fields_invalid")
        try:
            arguments = _JSON_OBJECT.validate_json(encoded)
        except ValidationError:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_question_arguments_invalid")
        invocation = step.context.model_copy(
            update={"invocation_id": stable_id("curation-question", step.request.job_id)}
        )
        result = self.dependencies.tool_host.execute(
            KnowledgeToolName.KNOWLEDGE_QUESTION.value,
            arguments,
            invocation,
        )
        updated = step.progress.record(
            step.decision_index,
            KnowledgeToolName.KNOWLEDGE_QUESTION,
            result,
        )
        if result.status in {ToolResultStatus.PENDING, ToolResultStatus.REPLAYED}:
            return updated, CurationTerminal(CurationRunStatus.AWAITING_ANSWER)
        return updated, CurationTerminal(
            CurationRunStatus.FAILED,
            result.error_code or "curation_question_failed",
        )

    def _apply_disposition(
        self,
        step: _CurationStep,
        intent: SourceDispositionIntent,
    ) -> CurationProgress | CurationTerminal | _DispositionConflict:
        operation_id = stable_id(
            "curation-disposition",
            step.request.job_id,
            intent.source_id,
        )
        try:
            receipt = self.dependencies.dispositions.apply(
                DispositionApplyRequest(intent, step.context, operation_id)
            )
        except SourceDispositionError as error:
            return CurationTerminal(CurationRunStatus.FAILED, str(error))
        except KnowledgePolicyError as error:
            return CurationTerminal(CurationRunStatus.FAILED, error.code)
        except RepositoryConflictError as error:
            return _DispositionConflict(error.code)
        return step.progress.record_disposition(receipt.operation_id, step.decision_index)

    @staticmethod
    def _conflict_observation(
        progress: CurationProgress,
        decision_index: int,
        error_code: str,
    ) -> CurationProgress:
        return progress.record(
            decision_index,
            None,
            ToolResult(
                schema="knowledge.tool-result.v1",
                status=ToolResultStatus.CONFLICT,
                error_code=error_code,
                operation_id=stable_id("curation-conflict", str(decision_index), error_code),
            ),
        )


__all__ = ["CurationRunner"]
