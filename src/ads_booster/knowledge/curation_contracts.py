from __future__ import annotations

# ruff: noqa: EM101, TC001
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Annotated, Literal, Self, override

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.knowledge.contract_types import (
    BoundedReason,
    BoundedText,
    KnowledgeContractModel,
    SourceCompleteness,
    SourceDisposition,
    UtcDatetime,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.operation_contracts import EventReceipt
from ads_booster.knowledge.operation_enums import CurationTarget
from ads_booster.knowledge.tool_contracts import KnowledgeToolName, ToolResult
from ads_booster.transport.json_types import JsonObject


@unique
class CurationDecisionAction(StrEnum):
    TOOL_CALL = "tool_call"
    FINISH = "finish"
    QUESTION = "question"


@unique
class CurationRunStatus(StrEnum):
    FINISHED = "finished"
    AWAITING_ANSWER = "awaiting_answer"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CurationExcerpt(KnowledgeContractModel):
    source_id: BoundedId
    revision_id: BoundedId
    segment_id: BoundedId | None = None
    locator: Annotated[str, Field(max_length=2_000)] = ""
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    completeness: SourceCompleteness


class CurationToolDefinition(KnowledgeContractModel):
    name: KnowledgeToolName
    input_schema: JsonObject


class CurationUserEvent(KnowledgeContractModel):
    evidence_ref: EvidenceRef
    authority_ref: AuthorityRef


class CurationRequest(KnowledgeContractModel):
    schema_version: Literal["knowledge.curation-request.v1"] = Field(alias="schema")
    job_id: BoundedId
    event_id: BoundedId
    event_revision: Annotated[int, Field(ge=1)]
    policy_version: BoundedId
    objective: BoundedText
    authenticated_user_event: CurationUserEvent | None = None
    excerpts: Annotated[tuple[CurationExcerpt, ...], Field(max_length=20)] = ()
    tool_catalog: Annotated[tuple[CurationToolDefinition, ...], Field(max_length=12)] = ()
    started_at: UtcDatetime


class SourceDispositionIntent(KnowledgeContractModel):
    source_id: BoundedId
    revision_id: BoundedId
    expected_admission_revision: Annotated[int, Field(ge=0)]
    disposition: SourceDisposition
    reason: BoundedReason

    @model_validator(mode="after")
    def require_decided_disposition(self) -> Self:
        if self.disposition is SourceDisposition.PENDING:
            raise PydanticCustomError(
                "curation_disposition_pending",
                "a terminal curation intent cannot retain pending disposition",
            )
        return self


class CurationDecision(KnowledgeContractModel):
    schema_version: Literal["knowledge.curation-decision.v1"] = Field(alias="schema")
    action: CurationDecisionAction
    tool_name: KnowledgeToolName | None = None
    tool_arguments_json: Annotated[str, Field(max_length=200_000)] | None = None
    question_arguments_json: Annotated[str, Field(max_length=20_000)] | None = None
    disposition_intent: SourceDispositionIntent | None = None
    targets: Annotated[tuple[CurationTarget, ...], Field(max_length=8)] = ()
    finish_summary: BoundedReason | None = None

    @model_validator(mode="after")
    def require_action_fields(self) -> Self:
        match self.action:
            case CurationDecisionAction.TOOL_CALL:
                valid = (
                    self.tool_name is not None
                    and self.tool_arguments_json is not None
                    and self.question_arguments_json is None
                    and self.finish_summary is None
                )
            case CurationDecisionAction.QUESTION:
                valid = (
                    self.tool_name is None
                    and self.tool_arguments_json is None
                    and self.question_arguments_json is not None
                    and self.finish_summary is None
                )
            case CurationDecisionAction.FINISH:
                valid = (
                    self.tool_name is None
                    and self.tool_arguments_json is None
                    and self.question_arguments_json is None
                    and self.finish_summary is not None
                )
        if not valid:
            raise PydanticCustomError(
                "curation_decision_fields_invalid",
                "curation decision fields must match its action",
            )
        if len(self.targets) != len(set(self.targets)):
            raise PydanticCustomError(
                "curation_targets_must_be_unique",
                "curation decision targets must be unique",
            )
        return self


class CurationBatchJobDecision(KnowledgeContractModel):
    job_id: BoundedId
    decision: CurationDecision


class CurationBatchDecision(KnowledgeContractModel):
    schema_version: Literal["knowledge.curation-batch-decision.v1"] = Field(alias="schema")
    batch_id: BoundedId
    decisions: Annotated[tuple[CurationBatchJobDecision, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def require_unique_jobs(self) -> Self:
        job_ids = tuple(item.job_id for item in self.decisions)
        if len(job_ids) != len(set(job_ids)):
            raise PydanticCustomError(
                "curation_batch_decision_jobs_must_be_unique",
                "a batch round must contain one decision per active job",
            )
        return self


class CurationObservation(KnowledgeContractModel):
    decision_index: Annotated[int, Field(ge=1, le=32)]
    tool_name: KnowledgeToolName | None = None
    result: ToolResult


class CurationBatchJobContext(KnowledgeContractModel):
    request: CurationRequest
    observations: Annotated[tuple[CurationObservation, ...], Field(max_length=32)] = ()


class CurationLimits(KnowledgeContractModel):
    max_decisions: Annotated[int, Field(ge=1, le=32)] = 8
    decision_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 120.0
    total_timeout_seconds: Annotated[float, Field(gt=0, le=3_600)] = 900.0
    max_search_calls: Annotated[int, Field(ge=0, le=20)] = 5
    max_fetch_calls: Annotated[int, Field(ge=0, le=40)] = 10
    max_conflict_redecisions: Annotated[int, Field(ge=0, le=8)] = 2


@dataclass(slots=True)
class CurationProviderError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


class CurationResult(KnowledgeContractModel):
    schema_version: Literal["knowledge.curation-result.v1"] = Field(alias="schema")
    job_id: BoundedId
    status: CurationRunStatus
    event_receipt: EventReceipt
    observations: Annotated[tuple[CurationObservation, ...], Field(max_length=32)] = ()
    decision_count: Annotated[int, Field(ge=0, le=32)]
    search_calls: Annotated[int, Field(ge=0, le=20)]
    fetch_calls: Annotated[int, Field(ge=0, le=40)]
    applied_operation_ids: Annotated[tuple[BoundedId, ...], Field(max_length=32)] = ()
    disposition: SourceDisposition | None = None
    error_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None


__all__ = [
    "CurationBatchDecision",
    "CurationBatchJobContext",
    "CurationBatchJobDecision",
    "CurationDecision",
    "CurationDecisionAction",
    "CurationExcerpt",
    "CurationLimits",
    "CurationObservation",
    "CurationProviderError",
    "CurationRequest",
    "CurationResult",
    "CurationRunStatus",
    "CurationToolDefinition",
    "SourceDispositionIntent",
]
