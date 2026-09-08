from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ads_booster.knowledge.contract_types import SourceDisposition
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationDecision,
    CurationLimits,
    CurationObservation,
    CurationRequest,
    CurationResult,
    CurationRunStatus,
)
from ads_booster.knowledge.curation_disposition import SourceDispositionPort
from ads_booster.knowledge.operation_contracts import EventReceipt
from ads_booster.knowledge.operation_enums import CurationTarget, OperationStatus
from ads_booster.knowledge.tool_contracts import (
    KnowledgeToolName,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.transport.json_types import JsonObject


class CurationDecisionProvider(Protocol):
    def decide(
        self,
        request: CurationRequest,
        observations: tuple[CurationObservation, ...],
        *,
        timeout_seconds: float,
    ) -> CurationDecision: ...


class CurationBatchDecisionProvider(CurationDecisionProvider, Protocol):
    def decide_batch(
        self,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision: ...


class CurationToolHost(Protocol):
    def execute(
        self,
        name: str,
        arguments: JsonObject,
        trusted_context: TrustedInvocationContext,
    ) -> ToolResult: ...

    def schemas(self) -> dict[KnowledgeToolName, JsonObject]: ...


class CancellationSignal(Protocol):
    def cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class CurationDependencies:
    provider: CurationBatchDecisionProvider
    tool_host: CurationToolHost
    dispositions: SourceDispositionPort


@dataclass(frozen=True, slots=True)
class CurationProgress:
    observations: tuple[CurationObservation, ...] = ()
    applied_operation_ids: tuple[str, ...] = ()
    decision_count: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    conflict_count: int = 0

    def record(
        self,
        decision_index: int,
        tool_name: KnowledgeToolName | None,
        result: ToolResult,
    ) -> CurationProgress:
        applied = self.applied_operation_ids
        if result.status in {ToolResultStatus.APPLIED, ToolResultStatus.REPLAYED}:
            applied = (*applied, result.operation_id)
        return CurationProgress(
            observations=(
                *self.observations,
                CurationObservation(
                    decision_index=decision_index,
                    tool_name=tool_name,
                    result=result,
                ),
            ),
            applied_operation_ids=applied,
            decision_count=decision_index,
            search_calls=self.search_calls
            + int(tool_name is KnowledgeToolName.SOURCE_SEARCH),
            fetch_calls=self.fetch_calls + int(tool_name is KnowledgeToolName.SOURCE_FETCH),
            conflict_count=self.conflict_count
            + int(result.status is ToolResultStatus.CONFLICT),
        )

    def record_disposition(self, operation_id: str, decision_index: int) -> CurationProgress:
        return CurationProgress(
            observations=self.observations,
            applied_operation_ids=(*self.applied_operation_ids, operation_id),
            decision_count=decision_index,
            search_calls=self.search_calls,
            fetch_calls=self.fetch_calls,
            conflict_count=self.conflict_count,
        )


@dataclass(frozen=True, slots=True)
class CurationTerminal:
    status: CurationRunStatus
    error_code: str | None = None
    targets: tuple[CurationTarget, ...] = ()
    disposition: SourceDisposition | None = None


def finish_result(
    request: CurationRequest,
    progress: CurationProgress,
    terminal: CurationTerminal,
) -> CurationResult:
    event_status = (
        OperationStatus.APPLIED
        if progress.applied_operation_ids
        else OperationStatus.REJECTED
    )
    if terminal.status is CurationRunStatus.AWAITING_ANSWER:
        event_status = OperationStatus.PENDING
    return CurationResult(
        schema="knowledge.curation-result.v1",
        job_id=request.job_id,
        status=terminal.status,
        event_receipt=EventReceipt(
            event_id=request.event_id,
            event_revision=request.event_revision,
            status=event_status,
            targets=terminal.targets,
            reason=terminal.error_code,
        ),
        observations=progress.observations,
        decision_count=progress.decision_count,
        search_calls=progress.search_calls,
        fetch_calls=progress.fetch_calls,
        applied_operation_ids=tuple(dict.fromkeys(progress.applied_operation_ids)),
        disposition=terminal.disposition,
        error_code=terminal.error_code,
    )


def tool_budget_error(
    name: KnowledgeToolName,
    progress: CurationProgress,
    limits: CurationLimits,
) -> str | None:
    match name:
        case KnowledgeToolName.SOURCE_SEARCH if progress.search_calls >= limits.max_search_calls:
            return "curation_search_budget_exhausted"
        case KnowledgeToolName.SOURCE_FETCH if progress.fetch_calls >= limits.max_fetch_calls:
            return "curation_fetch_budget_exhausted"
        case _:
            return None


__all__ = [
    "CancellationSignal",
    "CurationBatchDecisionProvider",
    "CurationDecisionProvider",
    "CurationDependencies",
    "CurationProgress",
    "CurationTerminal",
    "CurationToolHost",
    "finish_result",
    "tool_budget_error",
]
