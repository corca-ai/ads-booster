from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.knowledge.batch_curation import CurationBatchWork
from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
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
    MemoryApplyInput,
    MemoryCorrectInput,
    SkillApplyInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.knowledge.tool_support import error_result
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_BATCH_ROUNDS: Final = 8
_MAX_BATCH_ROUND_SECONDS: Final = 120.0
_MAX_BATCH_TOTAL_SECONDS: Final = 900.0
_MAX_BATCH_ITEMS: Final = 20
_LEARNING_TOOL_NAMES: Final[frozenset[KnowledgeToolName]] = frozenset(
    {
        KnowledgeToolName.MEMORY_GET,
        KnowledgeToolName.MEMORY_APPLY,
        KnowledgeToolName.MEMORY_CORRECT,
        KnowledgeToolName.SOURCE_READ,
        KnowledgeToolName.SKILL_LIST,
        KnowledgeToolName.SKILL_GET,
        KnowledgeToolName.SKILL_APPLY,
        KnowledgeToolName.KNOWLEDGE_QUESTION,
    }
)


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
    progress: CurationProgress = field(default_factory=CurationProgress)


@dataclass(frozen=True, slots=True)
class _BatchRound:
    batch_id: str
    active: dict[str, _BatchJobState]
    completed: dict[str, CurationResult]
    cancellation: CancellationSignal | None
    started: float
    total_seconds: float
    decision_index: int


@dataclass(frozen=True, slots=True)
class CurationRunner:
    dependencies: CurationDependencies
    limits: CurationLimits = field(default_factory=CurationLimits)

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
            round_ = _BatchRound(
                batch_id,
                active,
                completed,
                cancellation,
                started,
                total_seconds,
                decision_index,
            )
            if not self._run_batch_round(round_):
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

    def _run_batch_round(self, round_: _BatchRound) -> bool:
        terminal = self._batch_preflight(
            round_.cancellation,
            round_.started,
            round_.total_seconds,
        )
        if terminal is not None:
            self._finish_active(round_.active, round_.completed, terminal)
            return False
        outcome = self._batch_decision(round_)
        match outcome:
            case CurationTerminal():
                self._finish_active(round_.active, round_.completed, outcome)
                return False
            case CurationBatchDecision():
                terminal = self._advance_batch_jobs(round_, outcome)
                if terminal is not None:
                    self._finish_active(round_.active, round_.completed, terminal)
                    return False
                return bool(round_.active)

    def _batch_decision(
        self,
        round_: _BatchRound,
    ) -> CurationBatchDecision | CurationTerminal:
        remaining_seconds = round_.total_seconds - (time.monotonic() - round_.started)
        if remaining_seconds <= 0:
            return CurationTerminal(CurationRunStatus.BUDGET_EXHAUSTED, "curation_total_timeout")
        try:
            decision = self.dependencies.provider.decide_batch(
                round_.batch_id,
                tuple(
                    CurationBatchJobContext(
                        request=state.work.request,
                        observations=state.progress.observations,
                    )
                    for state in round_.active.values()
                ),
                timeout_seconds=min(
                    self.limits.decision_timeout_seconds,
                    _MAX_BATCH_ROUND_SECONDS,
                    remaining_seconds,
                ),
            )
        except CurationProviderError:
            return CurationTerminal(
                CurationRunStatus.PROVIDER_UNAVAILABLE,
                "knowledge_provider_batch_result_invalid",
            )
        decisions = {item.job_id: item.decision for item in decision.decisions}
        if (
            decision.batch_id != round_.batch_id
            or len(decision.decisions) != len(round_.active)
            or set(decisions) != set(round_.active)
        ):
            return CurationTerminal(
                CurationRunStatus.PROVIDER_UNAVAILABLE,
                "knowledge_provider_batch_result_invalid",
            )
        return decision

    def _advance_batch_jobs(
        self,
        round_: _BatchRound,
        batch_decision: CurationBatchDecision,
    ) -> CurationTerminal | None:
        decisions = {item.job_id: item.decision for item in batch_decision.decisions}
        for job_id, state in tuple(round_.active.items()):
            terminal = self._batch_preflight(
                round_.cancellation,
                round_.started,
                round_.total_seconds,
            )
            if terminal is not None:
                return terminal
            step = _CurationStep(
                state.work.request,
                state.work.trusted_context,
                state.progress,
                round_.decision_index,
            )
            advance = self._advance(step, decisions[job_id])
            if advance.terminal is None:
                round_.active[job_id] = _BatchJobState(state.work, advance.progress)
                continue
            round_.completed[job_id] = finish_result(
                state.work.request,
                advance.progress,
                advance.terminal,
            )
            del round_.active[job_id]
        return None

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
        schemas = self.dependencies.tool_host.schemas()
        if request.learning_purpose is not None:
            schemas = {
                name: schema for name, schema in schemas.items() if name in _LEARNING_TOOL_NAMES
            }
        return request.model_copy(
            update={
                "auto_memory_enabled": (
                    self.dependencies.memory is not None
                    and request.learning_purpose is None
                    and request.authenticated_user_event is not None
                    and request.authenticated_user_event.evidence_ref.scope.kind
                    in (ScopeKind.WORKSPACE, ScopeKind.CHANNEL)
                ),
                "tool_catalog": tuple(
                    CurationToolDefinition(name=name, input_schema=schema)
                    for name, schema in schemas.items()
                )
            }
        )

    @staticmethod
    def _batch_binding_error(items: tuple[CurationBatchWork, ...]) -> str | None:
        if not items:
            return None
        if len(items) > _MAX_BATCH_ITEMS:
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
                return self._advance_tool(step, decision)
            case CurationDecisionAction.REMEMBER:
                outcome = self._execute_remember(step, decision.memory_intent)
                return _tool_advance(step.progress, outcome)
            case CurationDecisionAction.QUESTION:
                return self._advance_question(step, decision)
            case CurationDecisionAction.FINISH:
                return self._advance_finish(step, decision)

    def _advance_tool(
        self,
        step: _CurationStep,
        decision: CurationDecision,
    ) -> _CurationAdvance:
        outcome = self._execute_tool(step, decision.tool_name, decision.tool_arguments_json)
        match outcome:
            case CurationTerminal():
                return _CurationAdvance(step.progress, outcome)
            case CurationProgress():
                return _CurationAdvance(outcome)

    def _advance_question(
        self,
        step: _CurationStep,
        decision: CurationDecision,
    ) -> _CurationAdvance:
        outcome = self._execute_question(step, decision.question_arguments_json)
        match outcome:
            case CurationTerminal():
                return _CurationAdvance(step.progress, outcome)
            case (CurationProgress() as progress, terminal):
                return _CurationAdvance(progress, terminal)

    def _advance_finish(
        self,
        step: _CurationStep,
        decision: CurationDecision,
    ) -> _CurationAdvance:
        intent = decision.disposition_intent
        if intent is None:
            return self._finish_advance(step.progress, decision, None)
        outcome = self._apply_disposition(step, intent)
        match outcome:
            case CurationTerminal():
                return _CurationAdvance(step.progress, outcome)
            case _DispositionConflict(error_code=error_code):
                return self._advance_disposition_conflict(step, error_code)
            case CurationProgress():
                return self._finish_advance(outcome, decision, intent)

    def _advance_disposition_conflict(
        self,
        step: _CurationStep,
        error_code: str,
    ) -> _CurationAdvance:
        progress = self._conflict_observation(step.progress, step.decision_index, error_code)
        if progress.conflict_count <= self.limits.max_conflict_redecisions:
            return _CurationAdvance(progress)
        return _CurationAdvance(
            progress,
            CurationTerminal(
                CurationRunStatus.BUDGET_EXHAUSTED,
                "curation_conflict_budget_exhausted",
            ),
        )

    @staticmethod
    def _finish_advance(
        progress: CurationProgress,
        decision: CurationDecision,
        intent: SourceDispositionIntent | None,
    ) -> _CurationAdvance:
        return _CurationAdvance(
            progress,
            CurationTerminal(
                CurationRunStatus.FINISHED,
                targets=decision.targets,
                disposition=None if intent is None else intent.disposition,
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
        outcome = self._tool_call(step, name, encoded)
        match outcome:
            case CurationTerminal():
                return outcome
            case (KnowledgeToolName() as name, dict() as arguments):
                return self._record_tool_execution(step, name, arguments)

    def _tool_call(
        self,
        step: _CurationStep,
        name: KnowledgeToolName | None,
        encoded: str | None,
    ) -> tuple[KnowledgeToolName, JsonObject] | CurationTerminal:
        if name is None or encoded is None:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_decision_fields_invalid")
        if step.request.learning_purpose is not None and name not in _LEARNING_TOOL_NAMES:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_learning_tool_forbidden")
        budget_error = tool_budget_error(name, step.progress, self.limits)
        if budget_error is not None:
            return CurationTerminal(CurationRunStatus.BUDGET_EXHAUSTED, budget_error)
        try:
            arguments = _JSON_OBJECT.validate_json(encoded)
        except ValidationError:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_tool_arguments_invalid")
        if learning_target_is_consumed(step.request, name, arguments):
            return CurationTerminal(CurationRunStatus.FINISHED, "curation_learning_target_consumed")
        return name, arguments

    def _record_tool_execution(
        self,
        step: _CurationStep,
        name: KnowledgeToolName,
        arguments: JsonObject,
    ) -> CurationProgress | CurationTerminal:
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
    ) -> tuple[CurationProgress, CurationTerminal | None] | CurationTerminal:
        if encoded is None:
            return CurationTerminal(CurationRunStatus.FAILED, "curation_decision_fields_invalid")
        try:
            arguments = _JSON_OBJECT.validate_json(encoded)
        except ValidationError:
            return (
                step.progress.record(
                    step.decision_index,
                    KnowledgeToolName.KNOWLEDGE_QUESTION,
                    error_result(
                        stable_id("curation-question", step.request.job_id),
                        "curation_question_arguments_invalid",
                    ),
                ),
                None,
            )
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
        if result.status is ToolResultStatus.REJECTED and result.error_code in {
            "tool_input_invalid",
            "tool_input_schema_mismatch",
            "question_evidence_not_found",
        }:
            return updated, None
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


def learning_target_is_consumed(
    request: CurationRequest,
    name: KnowledgeToolName,
    arguments: JsonObject,
) -> bool:
    review = request.learning_review
    if request.learning_purpose is None or review is None or not review.consumed_target_ids:
        return False
    return bool(_learning_mutation_targets(name, arguments) & set(review.consumed_target_ids))


def _learning_mutation_targets(
    name: KnowledgeToolName,
    arguments: JsonObject,
) -> frozenset[str]:
    try:
        match name:
            case KnowledgeToolName.SKILL_APPLY:
                request = SkillApplyInput.model_validate(arguments)
                return frozenset(operation.skill_id for operation in request.operations)
            case KnowledgeToolName.MEMORY_APPLY:
                request = MemoryApplyInput.model_validate(arguments)
                return frozenset(request.target_ids)
            case KnowledgeToolName.MEMORY_CORRECT:
                request = MemoryCorrectInput.model_validate(arguments)
                if request.replacement_entry is None:
                    return frozenset()
                return frozenset((request.replacement_entry.document_id,))
            case (
                KnowledgeToolName.KNOWLEDGE_SEARCH
                | KnowledgeToolName.KNOWLEDGE_GET
                | KnowledgeToolName.MEMORY_GET
                | KnowledgeToolName.MEMORY_EXPLAIN
                | KnowledgeToolName.SOURCE_READ
                | KnowledgeToolName.SOURCE_SEARCH
                | KnowledgeToolName.SOURCE_FETCH
                | KnowledgeToolName.KNOWLEDGE_APPLY
                | KnowledgeToolName.KNOWLEDGE_SCHEDULE
                | KnowledgeToolName.KNOWLEDGE_QUESTION
                | KnowledgeToolName.SKILL_LIST
                | KnowledgeToolName.SKILL_GET
            ):
                return frozenset()
    except ValidationError:
        return frozenset()


__all__ = ["CurationRunner", "learning_target_is_consumed"]
