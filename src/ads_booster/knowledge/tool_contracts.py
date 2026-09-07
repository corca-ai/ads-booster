from __future__ import annotations

# ruff: noqa: EM101, TC001, TC003
from datetime import date
from enum import StrEnum, unique
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import Locale, Sha256Digest
from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
from ads_booster.knowledge.contract_types import (
    BoundedReason,
    BoundedText,
    GrantCapability,
    KnowledgeContractModel,
    MemoryKind,
    SourceCompleteness,
    SourceDisposition,
    UtcDatetime,
)
from ads_booster.knowledge.evidence_contracts import AuthenticatedEvent
from ads_booster.knowledge.governance_contracts import ConstraintBinding, TaskOverlay
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryEntry, MemoryRevision
from ads_booster.knowledge.operation_contracts import (
    KnowledgeOperation,
    MemoryExplanation,
    MemoryOperation,
    OperationReceipt,
)
from ads_booster.knowledge.operation_enums import (
    CorrectionScope,
    CorrectionStatus,
    JobKind,
    JobPriority,
)
from ads_booster.knowledge.retrieval import SearchCorpus
from ads_booster.knowledge.scope_contracts import ActorContext
from ads_booster.knowledge.source_contracts import ConversationEvent, SourceSegment
from ads_booster.knowledge.web_search import ExternalSearchPolicy, SearchBudget, SearchDomain
from ads_booster.knowledge.wiki_contracts import KnowledgeRevision, WikiPage

MAX_TOOL_CLAIMS = 20
MAX_TOOL_RESULTS = 20
MAX_SOURCE_RANGE = 20_000


@unique
class KnowledgeToolName(StrEnum):
    KNOWLEDGE_SEARCH = "knowledge_search"
    KNOWLEDGE_GET = "knowledge_get"
    MEMORY_GET = "memory_get"
    MEMORY_APPLY = "memory_apply"
    MEMORY_EXPLAIN = "memory_explain"
    MEMORY_CORRECT = "memory_correct"
    SOURCE_READ = "source_read"
    SOURCE_SEARCH = "source_search"
    SOURCE_FETCH = "source_fetch"
    KNOWLEDGE_APPLY = "knowledge_apply"
    KNOWLEDGE_SCHEDULE = "knowledge_schedule"
    KNOWLEDGE_QUESTION = "knowledge_question"


@unique
class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    APPLIED = "applied"
    REPLAYED = "replayed"
    PENDING = "pending"
    NO_RESULTS = "no_results"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@unique
class QuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    CANCELLED = "cancelled"


@unique
class ProposalTargetKind(StrEnum):
    WIKI = "wiki"
    MEMORY = "memory"
    SOUL = "soul"


class AttributeFilter(KnowledgeContractModel):
    key: Annotated[str, Field(min_length=1, max_length=100)]
    value: Annotated[str, Field(min_length=1, max_length=500)]


class KnowledgeSearchInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.search.v1"] = Field(alias="schema")
    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    corpus: SearchCorpus = SearchCorpus.ALL
    attributes: Annotated[tuple[AttributeFilter, ...], Field(max_length=32)] = ()
    title_or_alias: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_TOOL_RESULTS)] = 8

    @model_validator(mode="after")
    def require_unique_attributes(self) -> Self:
        keys = tuple(item.key for item in self.attributes)
        if len(keys) != len(set(keys)):
            raise PydanticCustomError(
                "knowledge_search_attribute_duplicate",
                "attribute filter keys must be unique",
            )
        return self


class KnowledgeGetInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.get.v1"] = Field(alias="schema")
    page_id: BoundedId
    revision_id: BoundedId | None = None
    claim_ids: Annotated[tuple[BoundedId, ...], Field(max_length=MAX_TOOL_CLAIMS)] = ()


class MemoryGetInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.memory-get.v1"] = Field(alias="schema")
    kind: MemoryKind
    brand_id: BoundedId | None = None
    local_date: date | None = None
    revision_id: BoundedId | None = None

    @model_validator(mode="after")
    def require_kind_selector(self) -> Self:
        match self.kind:
            case MemoryKind.SOUL:
                if self.brand_id is None or self.local_date is not None:
                    raise PydanticCustomError(
                        "memory_tool_soul_selector_invalid",
                        "soul selection requires a brand and forbids a date",
                    )
            case MemoryKind.DAILY:
                if self.local_date is None or self.brand_id is not None:
                    raise PydanticCustomError(
                        "memory_tool_daily_selector_invalid",
                        "daily selection requires a date and forbids a brand",
                    )
            case MemoryKind.TEAM | MemoryKind.CORE:
                if self.brand_id is not None or self.local_date is not None:
                    raise PydanticCustomError(
                        "memory_tool_selector_invalid",
                        "team and core selection forbid brand and date",
                    )
        return self


class MemoryRevisionPayload(KnowledgeContractModel):
    operation: MemoryOperation
    document: MemoryDocument
    revision: MemoryRevision
    entries: Annotated[tuple[MemoryEntry, ...], Field(max_length=MAX_TOOL_CLAIMS)] = ()
    constraints: Annotated[tuple[ConstraintBinding, ...], Field(max_length=MAX_TOOL_CLAIMS)] = ()
    body: Annotated[str, Field(max_length=100_000)]


class MemoryApplyInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.memory-apply.v1"] = Field(alias="schema")
    operation_id: BoundedId
    changes: Annotated[tuple[MemoryRevisionPayload, ...], Field(min_length=1, max_length=20)]
    adoption_receipt_ids: Annotated[tuple[BoundedId, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def require_operation_binding(self) -> Self:
        if any(item.operation.operation_id != self.operation_id for item in self.changes):
            raise PydanticCustomError(
                "memory_tool_operation_mismatch",
                "every memory operation must match the request operation",
            )
        return self


class PageRevisionPayload(KnowledgeContractModel):
    page: WikiPage
    revision: KnowledgeRevision
    body: Annotated[str, Field(max_length=200_000)]


class PageRedirectPayload(KnowledgeContractModel):
    from_page_id: BoundedId
    to_page_id: BoundedId


class KnowledgeApplyInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.apply.v1"] = Field(alias="schema")
    operation_id: BoundedId
    page_operation: KnowledgeOperation | None = None
    pages: Annotated[tuple[PageRevisionPayload, ...], Field(max_length=20)] = ()
    redirects: Annotated[tuple[PageRedirectPayload, ...], Field(max_length=20)] = ()
    memory_changes: Annotated[tuple[MemoryRevisionPayload, ...], Field(max_length=20)] = ()
    adoption_receipt_ids: Annotated[tuple[BoundedId, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def require_bounded_change(self) -> Self:
        operations = (() if self.page_operation is None else (self.page_operation,)) + tuple(
            item.operation for item in self.memory_changes
        )
        if not operations or any(item.operation_id != self.operation_id for item in operations):
            raise PydanticCustomError(
                "knowledge_tool_operation_mismatch",
                "every operation must match the request operation",
            )
        claim_count = sum(len(item.revision.claims) for item in self.pages)
        if self.page_operation is not None:
            claim_count = max(claim_count, len(self.page_operation.claim_ids))
        if claim_count > MAX_TOOL_CLAIMS:
            raise PydanticCustomError(
                "knowledge_tool_claim_limit",
                "one tool change cannot carry more than 20 claims",
            )
        redirect_sources = tuple(item.from_page_id for item in self.redirects)
        if len(redirect_sources) != len(set(redirect_sources)):
            raise PydanticCustomError(
                "knowledge_tool_redirect_duplicate",
                "redirect sources must be unique",
            )
        return self


class MemoryExplainInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.memory-explain.v1"] = Field(alias="schema")
    target_id: BoundedId
    task_receipt_id: BoundedId | None = None


class MemoryCorrectInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.memory-correct.v1"] = Field(alias="schema")
    operation_id: BoundedId
    target_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]
    correction_text: BoundedText
    scope: CorrectionScope
    brand_id: BoundedId | None = None
    task_id: BoundedId | None = None
    authenticated_event_ref: BoundedId

    @model_validator(mode="after")
    def require_scope_fields(self) -> Self:
        if self.scope is CorrectionScope.TASK_ONLY and self.task_id is None:
            raise PydanticCustomError(
                "task_correction_requires_task",
                "task-only corrections require a task reference",
            )
        if self.scope is CorrectionScope.TEAM and self.task_id is not None:
            raise PydanticCustomError(
                "team_correction_forbids_task",
                "team corrections cannot carry a task reference",
            )
        return self


class TextRange(KnowledgeContractModel):
    start: Annotated[int, Field(ge=0, le=10_000_000)]
    end: Annotated[int, Field(gt=0, le=10_000_000)]

    @model_validator(mode="after")
    def require_ordered_range(self) -> Self:
        if self.end <= self.start or self.end - self.start > MAX_SOURCE_RANGE:
            raise PydanticCustomError(
                "source_range_invalid",
                "text ranges must be ordered and at most 20000 characters",
            )
        return self


class SourceReadInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.source-read.v1"] = Field(alias="schema")
    source_id: BoundedId
    revision_id: BoundedId | None = None
    segment_ids: Annotated[tuple[BoundedId, ...], Field(max_length=20)] = ()
    text_range: TextRange | None = None

    @model_validator(mode="after")
    def require_one_selector(self) -> Self:
        if bool(self.segment_ids) == (self.text_range is not None):
            raise PydanticCustomError(
                "source_read_selector_invalid",
                "source read requires segment IDs or one text range",
            )
        return self


class SourceSearchInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.source-search.v1"] = Field(alias="schema")
    query: Annotated[str, Field(min_length=1, max_length=500)]
    locale: Locale | None = None
    domains: Annotated[tuple[SearchDomain, ...], Field(max_length=5)] = ()
    limit: Annotated[int, Field(ge=1, le=5)] = 5


class SourceFetchInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.source-fetch.v1"] = Field(alias="schema")
    url: Annotated[str, Field(min_length=1, max_length=4_096)]
    source_id: BoundedId | None = None


class KnowledgeScheduleInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.schedule.v1"] = Field(alias="schema")
    operation_id: BoundedId
    kind: JobKind
    priority: JobPriority = JobPriority.ROUTINE
    targets: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]
    due_at: UtcDatetime
    purpose: BoundedReason
    triggers: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]


class ScheduledKnowledgeRequest(KnowledgeContractModel):
    job_id: BoundedId
    operation_id: BoundedId
    workspace_id: BoundedId
    targets: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]
    purpose: BoundedReason
    triggers: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]
    capability_epoch: Annotated[int, Field(ge=1)]


class PendingProposal(KnowledgeContractModel):
    proposal_id: BoundedId
    target_kind: ProposalTargetKind
    target_id: BoundedId
    expected_revision_id: BoundedId
    brand_id: BoundedId | None = None

    @model_validator(mode="after")
    def require_brand_for_soul(self) -> Self:
        if self.target_kind is ProposalTargetKind.SOUL and self.brand_id is None:
            raise PydanticCustomError(
                "soul_proposal_requires_brand",
                "soul proposals require a brand",
            )
        if self.target_kind is not ProposalTargetKind.SOUL and self.brand_id is not None:
            raise PydanticCustomError(
                "non_soul_proposal_forbids_brand",
                "only soul proposals carry a brand",
            )
        return self


class KnowledgeQuestionInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.question.v1"] = Field(alias="schema")
    question_id: BoundedId
    problem: BoundedText
    evidence_ids: Annotated[tuple[BoundedId, ...], Field(max_length=20)] = ()
    checks_tried: Annotated[tuple[BoundedReason, ...], Field(max_length=20)] = ()
    recommendation: BoundedReason
    pending_proposal: PendingProposal | None = None


class TrustedSourceCapability(KnowledgeContractModel):
    capability_id: BoundedId
    workspace_id: BoundedId
    actor_ref: BoundedId
    source_id: BoundedId
    revision_id: BoundedId
    segment_ids: Annotated[tuple[BoundedId, ...], Field(max_length=256)] = ()
    text_ranges: Annotated[tuple[TextRange, ...], Field(max_length=20)] = ()
    allows_unadmitted_read: bool = False
    expires_at: UtcDatetime | None = None


class TrustedInvocationContext(KnowledgeContractModel):
    """Server-created authority that is never parsed from model tool arguments."""

    invocation_id: BoundedId
    actor: ActorContext
    run_binding_id: BoundedId
    run_id: BoundedId
    task_id: BoundedId | None = None
    job_id: BoundedId | None = None
    brand_id: BoundedId | None = None
    capability_epoch: Annotated[int, Field(ge=1)]
    source_capabilities: Annotated[tuple[TrustedSourceCapability, ...], Field(max_length=256)] = ()
    source_fetch_event: ConversationEvent | None = None
    search_policy: ExternalSearchPolicy | None = None
    search_budget: SearchBudget | None = None
    invoked_at: UtcDatetime

    @model_validator(mode="after")
    def require_trusted_bindings(self) -> Self:
        if self.capability_epoch != self.actor.policy_epoch:
            raise PydanticCustomError(
                "invocation_policy_epoch_mismatch",
                "invocation capability epoch must match the authenticated actor",
            )
        if any(
            item.workspace_id != self.actor.workspace_id or item.actor_ref != self.actor.actor_id
            for item in self.source_capabilities
        ):
            raise PydanticCustomError(
                "source_capability_actor_mismatch",
                "source capabilities must match the authenticated actor and workspace",
            )
        return self


class KnowledgeSearchHit(KnowledgeContractModel):
    rank: Annotated[int, Field(ge=1, le=MAX_TOOL_RESULTS)]
    kind: SearchCorpus
    entity_id: BoundedId
    revision_id: BoundedId
    citation_id: Annotated[str, Field(min_length=1, max_length=500)]
    title: Annotated[str, Field(max_length=1_000)]
    snippet: Annotated[str, Field(max_length=1_200)]
    relevance_micros: Annotated[int, Field(ge=0)]
    related_page_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    needs_review: bool = False
    claim_id: BoundedId | None = None
    conflict_group_id: BoundedId | None = None
    conflict_role: Literal["claim", "counter_claim", "counter_evidence"] | None = None


class KnowledgeSearchData(KnowledgeContractModel):
    kind: Literal["knowledge_search"] = "knowledge_search"
    hits: Annotated[tuple[KnowledgeSearchHit, ...], Field(max_length=MAX_TOOL_RESULTS)]
    retrieval_status: str
    index_pending: bool
    total_count: Annotated[int, Field(ge=0)]


class KnowledgePageData(KnowledgeContractModel):
    kind: Literal["knowledge_page"] = "knowledge_page"
    page: WikiPage
    revision: KnowledgeRevision
    markdown: Annotated[str, Field(max_length=200_000)]


class MemoryData(KnowledgeContractModel):
    kind: Literal["memory"] = "memory"
    document: MemoryDocument
    revision: MemoryRevision
    entries: Annotated[tuple[MemoryEntry, ...], Field(max_length=512)]
    markdown: Annotated[str, Field(max_length=100_000)]
    view_pending: bool


class ApplyData(KnowledgeContractModel):
    kind: Literal["apply"] = "apply"
    receipt: OperationReceipt


class ExplanationData(KnowledgeContractModel):
    kind: Literal["explanation"] = "explanation"
    explanation: MemoryExplanation


class CorrectionData(KnowledgeContractModel):
    kind: Literal["correction"] = "correction"
    correction_id: BoundedId
    status: CorrectionStatus
    overlay: TaskOverlay | None = None
    question_id: BoundedId | None = None
    replayed: bool = False


class SourceExcerpt(KnowledgeContractModel):
    segment: SourceSegment | None = None
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    text: Annotated[str, Field(max_length=20_000)]


class SourceReadData(KnowledgeContractModel):
    kind: Literal["source"] = "source"
    source_id: BoundedId
    revision_id: BoundedId
    disposition: SourceDisposition
    completeness: SourceCompleteness
    excerpts: Annotated[tuple[SourceExcerpt, ...], Field(max_length=20)]


class SourceDiscoveryCandidateData(KnowledgeContractModel):
    url: Annotated[str, Field(min_length=1, max_length=2_048)]
    title: Annotated[str, Field(max_length=300)]
    snippet: Annotated[str, Field(max_length=2_000)]
    evidence_eligible: Literal[False] = False


class SourceSearchData(KnowledgeContractModel):
    kind: Literal["source_search"] = "source_search"
    discovery_id: BoundedId | None = None
    provider: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    fetched_at: UtcDatetime | None = None
    candidates: Annotated[tuple[SourceDiscoveryCandidateData, ...], Field(max_length=5)] = ()


class SourceFetchData(KnowledgeContractModel):
    kind: Literal["source_fetch"] = "source_fetch"
    source_id: BoundedId
    revision_id: BoundedId
    curation_job_id: BoundedId
    index_operation_id: BoundedId
    replayed: bool


class ScheduleData(KnowledgeContractModel):
    kind: Literal["schedule"] = "schedule"
    job_id: BoundedId
    replayed: bool


class QuestionRecord(KnowledgeContractModel):
    question_id: BoundedId
    workspace_id: BoundedId
    actor_ref: BoundedId
    problem: BoundedText
    evidence_ids: Annotated[tuple[BoundedId, ...], Field(max_length=20)] = ()
    checks_tried: Annotated[tuple[BoundedReason, ...], Field(max_length=20)] = ()
    recommendation: BoundedReason
    pending_proposal: PendingProposal | None = None
    status: QuestionStatus
    created_at: UtcDatetime
    answer_event_id: BoundedId | None = None
    answer: BoundedText | None = None
    answered_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def require_answer_lifecycle(self) -> Self:
        answered = self.status is QuestionStatus.ANSWERED
        fields_present = (
            self.answer_event_id is not None
            and self.answer is not None
            and self.answered_at is not None
        )
        if answered != fields_present:
            raise PydanticCustomError(
                "question_answer_state_invalid",
                "answered questions require a complete trusted answer binding",
            )
        return self


class QuestionData(KnowledgeContractModel):
    kind: Literal["question"] = "question"
    question: QuestionRecord


class TrustedQuestionAnswer(KnowledgeContractModel):
    """Trusted adapter input; never accepted through ToolHost.execute arguments."""

    question_id: BoundedId
    authenticated_event: AuthenticatedEvent
    answer: BoundedText
    explicitly_adopts: bool = False
    answered_at: UtcDatetime


type ToolData = (
    KnowledgeSearchData
    | KnowledgePageData
    | MemoryData
    | ApplyData
    | ExplanationData
    | CorrectionData
    | SourceReadData
    | SourceSearchData
    | SourceFetchData
    | ScheduleData
    | QuestionData
)


class ToolResult(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool-result.v1"] = Field(alias="schema")
    status: ToolResultStatus
    data: ToolData | None = Field(default=None, discriminator="kind")
    error_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    retryable: bool = False
    operation_id: BoundedId


type ToolInput = (
    KnowledgeSearchInput
    | KnowledgeGetInput
    | MemoryGetInput
    | MemoryApplyInput
    | MemoryExplainInput
    | MemoryCorrectInput
    | SourceReadInput
    | SourceSearchInput
    | SourceFetchInput
    | KnowledgeApplyInput
    | KnowledgeScheduleInput
    | KnowledgeQuestionInput
)


class ToolCatalogEntry(KnowledgeContractModel):
    name: KnowledgeToolName
    input_schema_sha256: Sha256Digest
    required_capability: GrantCapability


__all__ = [
    "ApplyData",
    "AttributeFilter",
    "CorrectionData",
    "ExplanationData",
    "ExplicitAdoptionReceipt",
    "KnowledgeApplyInput",
    "KnowledgeGetInput",
    "KnowledgePageData",
    "KnowledgeQuestionInput",
    "KnowledgeScheduleInput",
    "KnowledgeSearchData",
    "KnowledgeSearchHit",
    "KnowledgeSearchInput",
    "KnowledgeToolName",
    "MemoryApplyInput",
    "MemoryCorrectInput",
    "MemoryData",
    "MemoryExplainInput",
    "MemoryGetInput",
    "MemoryRevisionPayload",
    "PageRedirectPayload",
    "PageRevisionPayload",
    "PendingProposal",
    "ProposalTargetKind",
    "QuestionData",
    "QuestionRecord",
    "QuestionStatus",
    "ScheduleData",
    "ScheduledKnowledgeRequest",
    "SourceDiscoveryCandidateData",
    "SourceExcerpt",
    "SourceFetchData",
    "SourceFetchInput",
    "SourceReadData",
    "SourceReadInput",
    "SourceSearchData",
    "SourceSearchInput",
    "TextRange",
    "ToolCatalogEntry",
    "ToolData",
    "ToolInput",
    "ToolResult",
    "ToolResultStatus",
    "TrustedInvocationContext",
    "TrustedQuestionAnswer",
    "TrustedSourceCapability",
]
