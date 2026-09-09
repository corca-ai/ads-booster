from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final, Literal, Self, cast

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.knowledge.evidence_contracts import EvidenceRef  # noqa: TC001
from ads_booster.knowledge.operation_enums import SkillOrigin  # noqa: TC001


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise PydanticCustomError(
            _UTC_ERROR_TYPE,
            _UTC_ERROR_MESSAGE,
        )
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
BoundedContextText = Annotated[str, Field(min_length=1, max_length=20_000)]
_UTC_ERROR_TYPE: Final = "knowledge_timestamp_not_utc"
_UTC_ERROR_MESSAGE: Final = "knowledge timestamps must use UTC"
_RESERVE_ERROR_TYPE: Final = "context_reserve_exhausts_budget"
_RESERVE_ERROR_MESSAGE: Final = "context reserves must leave input capacity"
_COUNT_ERROR_TYPE: Final = "context_token_total_mismatch"
_COUNT_ERROR_MESSAGE: Final = "context token counts must add to the total"


class KnowledgeSelectionModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


@unique
class KnowledgeActionKind(StrEnum):
    CONTENT_WRITE = "content_write"
    CONTENT_REWRITE = "content_rewrite"
    CONTENT_EVALUATE = "content_evaluate"
    RESEARCH = "research"
    INGEST = "ingest"
    INDEX = "index"
    TEAM_CHAT = "team_chat"


@unique
class VoiceStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    CONFIGURED = "configured"
    VOICE_UNCONFIGURED = "voice_unconfigured"
    REQUIRED_VOICE_UNAVAILABLE = "required_voice_unavailable"
    BRAND_UNRESOLVED = "brand_unresolved"


@unique
class RetrievalStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    INDEX_PENDING = "index_pending"
    UNAVAILABLE = "unavailable"


@unique
class ContextExclusionReason(StrEnum):
    BUDGET = "budget"
    IRRELEVANT = "irrelevant"
    EXPIRED = "expired"
    STALE = "stale"
    RESTRICTED = "restricted"
    TOMBSTONED = "tombstoned"
    INTEGRITY_ERROR = "integrity_error"
    SUMMARY_PENDING = "summary_pending"


class ContextBudget(KnowledgeSelectionModel):
    max_input_tokens: Annotated[int, Field(ge=1, le=12_000)]
    output_reserve: Annotated[int, Field(ge=0, le=12_000)] = 0
    tool_reserve: Annotated[int, Field(ge=0, le=12_000)] = 0

    @model_validator(mode="after")
    def require_reserves_within_budget(self) -> Self:
        if self.output_reserve + self.tool_reserve >= self.max_input_tokens:
            raise PydanticCustomError(
                _RESERVE_ERROR_TYPE,
                _RESERVE_ERROR_MESSAGE,
            )
        return self


class ContextRequest(KnowledgeSelectionModel):
    """Model-visible task request; trusted identity is supplied separately."""

    schema_version: Literal["knowledge.context-request.v1"] = Field(alias="schema")
    request_id: BoundedId
    action_kind: KnowledgeActionKind
    task_ref: BoundedId
    brand_ref: BoundedId | None = None
    query: BoundedContextText
    required_context: bool
    budget: ContextBudget


class SelectedMemoryRevision(KnowledgeSelectionModel):
    document_id: BoundedId
    revision_id: BoundedId
    kind: Literal["team", "soul", "core", "daily", "user"]
    entry_ids: Annotated[tuple[BoundedId, ...], Field(max_length=256)] = ()
    content_sha256: Sha256Digest


class SelectedWikiClaim(KnowledgeSelectionModel):
    page_id: BoundedId
    revision_id: BoundedId
    claim_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=128)]
    content_sha256: Sha256Digest


class SelectedSourceRevision(KnowledgeSelectionModel):
    source_id: BoundedId
    revision_id: BoundedId
    segment_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    content_sha256: Sha256Digest


class SelectedSkillSourceRevision(KnowledgeSelectionModel):
    """Current source head retained by a selected skill without making it retrieved context."""

    source_id: BoundedId
    revision_id: BoundedId
    content_sha256: Sha256Digest


class SelectedSkillRevision(KnowledgeSelectionModel):
    """Metadata-only effective skill selection; full procedure text remains behind skill_get."""

    skill_id: BoundedId
    revision_id: BoundedId
    content_sha256: Sha256Digest
    origin: SkillOrigin
    protected: bool
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()
    source_revisions: Annotated[tuple[SelectedSkillSourceRevision, ...], Field(max_length=128)] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_digest(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Omit empty additive source dependencies from existing selected-skill digests."""
        result = cast("dict[str, object]", handler(self))
        if not self.source_refs:
            _ = result.pop("source_refs", None)
        if not self.source_revisions:
            _ = result.pop("source_revisions", None)
        return result


class SelectedConstraint(KnowledgeSelectionModel):
    constraint_id: BoundedId
    authority_ref: BoundedId
    revision_id: BoundedId


class SelectedSoulExample(KnowledgeSelectionModel):
    page_id: BoundedId
    claim_id: BoundedId
    revision_id: BoundedId


class ContextExclusion(KnowledgeSelectionModel):
    reference_id: BoundedId
    reason: ContextExclusionReason


class ContextTokenCounts(KnowledgeSelectionModel):
    required_tokens: Annotated[int, Field(ge=0, le=1_000_000)]
    selected_reference_tokens: Annotated[int, Field(ge=0, le=1_000_000)]
    total_input_tokens: Annotated[int, Field(ge=0, le=1_000_000)]

    @model_validator(mode="after")
    def require_total(self) -> Self:
        if self.required_tokens + self.selected_reference_tokens != self.total_input_tokens:
            raise PydanticCustomError(
                _COUNT_ERROR_TYPE,
                _COUNT_ERROR_MESSAGE,
            )
        return self


class ContextReceipt(KnowledgeSelectionModel):
    """Concrete selection and provenance receipt; it makes no semantic truth claim."""

    schema_version: Literal["knowledge.context-receipt.v1"] = Field(alias="schema")
    receipt_id: BoundedId
    task_ref: BoundedId
    scoped_actor_ref: BoundedId
    team_id: BoundedId
    policy_version: BoundedId
    action_kind: KnowledgeActionKind
    resolved_brand_ref: BoundedId | None = None
    brand_catalog_revision: Annotated[int | None, Field(ge=1)] = None
    soul_revision_id: BoundedId | None = None
    soul_core_entry_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    soul_example_refs: Annotated[tuple[SelectedSoulExample, ...], Field(max_length=32)] = ()
    voice_status: VoiceStatus
    required_constraints: Annotated[tuple[SelectedConstraint, ...], Field(max_length=128)] = ()
    selected_memory_revisions: Annotated[
        tuple[SelectedMemoryRevision, ...], Field(max_length=128)
    ] = ()
    selected_wiki_claims: Annotated[tuple[SelectedWikiClaim, ...], Field(max_length=256)] = ()
    selected_source_revisions: Annotated[
        tuple[SelectedSourceRevision, ...], Field(max_length=256)
    ] = ()
    selected_skill_revisions: Annotated[
        tuple[SelectedSkillRevision, ...], Field(max_length=256)
    ] = ()
    canonical_dedup_ids: Annotated[tuple[BoundedId, ...], Field(max_length=512)] = ()
    exclusions: Annotated[tuple[ContextExclusion, ...], Field(max_length=512)] = ()
    summary_pending_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    token_counts: ContextTokenCounts
    retrieval_status: RetrievalStatus
    created_at: UtcDatetime

    @model_serializer(mode="wrap")
    def preserve_legacy_digest(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Omit an empty additive selection from persisted v1 receipt digests."""
        result = cast("dict[str, object]", handler(self))
        if not self.selected_skill_revisions:
            _ = result.pop("selected_skill_revisions", None)
        return result


__all__ = [
    "ContextBudget",
    "ContextExclusion",
    "ContextExclusionReason",
    "ContextReceipt",
    "ContextRequest",
    "ContextTokenCounts",
    "KnowledgeActionKind",
    "RetrievalStatus",
    "SelectedConstraint",
    "SelectedMemoryRevision",
    "SelectedSkillRevision",
    "SelectedSkillSourceRevision",
    "SelectedSoulExample",
    "SelectedSourceRevision",
    "SelectedWikiClaim",
    "VoiceStatus",
]
