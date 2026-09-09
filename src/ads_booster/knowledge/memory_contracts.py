from __future__ import annotations

# ruff: noqa: EM101, TC001, TC003
from datetime import date
from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import (
    BoundedReason,
    BoundedText,
    DependencyState,
    IanaTimeZone,
    KnowledgeContractModel,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    ScopeKind,
    SoulSection,
    UsageRole,
    UtcDatetime,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.scope_contracts import AccessScope


def _scope_absent(value: AccessScope | None) -> bool:
    return value is None


class MemoryDocument(KnowledgeContractModel):
    document_id: BoundedId
    workspace_id: BoundedId
    kind: MemoryKind
    brand_id: BoundedId | None = None
    local_date: date | None = None
    timezone: IanaTimeZone
    head_revision_id: BoundedId
    scope: AccessScope | None = Field(default=None, exclude_if=_scope_absent)

    @property
    def owned_scope(self) -> AccessScope:
        return self.scope or AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=self.workspace_id)

    @model_validator(mode="after")
    def require_owned_scope(self) -> Self:
        if self.owned_scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "memory_scope_workspace_mismatch", "memory scope must match its workspace"
            )
        match self.kind:
            case MemoryKind.USER:
                if self.owned_scope.kind is not ScopeKind.CHANNEL_MEMBER:
                    raise PydanticCustomError(
                        "user_memory_scope_required",
                        "user memory requires channel member ownership",
                    )
            case MemoryKind.TEAM | MemoryKind.CORE | MemoryKind.DAILY | MemoryKind.SOUL:
                if self.owned_scope.kind not in (ScopeKind.WORKSPACE, ScopeKind.CHANNEL):
                    raise PydanticCustomError(
                        "memory_scope_private",
                        "shared memory requires workspace or channel ownership",
                    )
        return self

    @model_validator(mode="after")
    def require_kind_identity(self) -> Self:
        match self.kind:  # noqa: MATCH_OK
            case MemoryKind.SOUL:
                if self.brand_id is None or self.local_date is not None:
                    raise PydanticCustomError(
                        "invalid_memory_brand",
                        "soul memory requires a brand and forbids a date",
                    )
            case MemoryKind.DAILY:
                if self.brand_id is not None or self.local_date is None:
                    raise PydanticCustomError(
                        "invalid_memory_date",
                        "daily memory requires a date and forbids a brand",
                    )
            case MemoryKind.TEAM | MemoryKind.CORE | MemoryKind.USER:
                if self.brand_id is not None:
                    raise PydanticCustomError(
                        "invalid_memory_brand",
                        "team, core and user memory forbid a brand",
                    )
                if self.local_date is not None:
                    raise PydanticCustomError(
                        "invalid_memory_date",
                        "team, core and user memory forbid a date",
                    )
        return self


class WikiSummaryRef(KnowledgeContractModel):
    page_id: BoundedId
    claim_id: BoundedId
    revision_id: BoundedId
    semantic_fingerprint: Sha256Digest


class SoulExampleRef(KnowledgeContractModel):
    page_id: BoundedId
    claim_id: BoundedId
    revision_id: BoundedId


class MemoryEntry(KnowledgeContractModel):
    entry_id: BoundedId
    document_id: BoundedId
    document_kind: MemoryKind
    text: BoundedText
    kind: MemoryEntryKind
    status: MemoryStatus
    dependency_state: DependencyState
    origin: MemoryOrigin
    usage_role: UsageRole
    scope: AccessScope
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1, max_length=128)]
    wiki_ref: WikiSummaryRef | None = None
    applicability: AppliesTo | None = None
    expires_at: UtcDatetime | None = None
    review_after: UtcDatetime | None = None
    admission_reason: BoundedReason
    authority_ref: AuthorityRef | None = None
    soul_section: SoulSection | None = None
    example_refs: Annotated[tuple[SoulExampleRef, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def require_canonical_owner(self) -> Self:
        match self.origin:  # noqa: MATCH_OK
            case MemoryOrigin.DIRECT:
                if self.wiki_ref is not None:
                    raise PydanticCustomError(
                        "direct_memory_forbids_wiki_ref",
                        "direct memory cannot claim Wiki summary ownership",
                    )
            case MemoryOrigin.WIKI_SUMMARY:
                if self.wiki_ref is None:
                    raise PydanticCustomError(
                        "wiki_summary_requires_ref",
                        "Wiki summaries require a current Wiki reference",
                    )
        return self

    @model_validator(mode="after")
    def require_constraint_authority(self) -> Self:
        match self.usage_role:  # noqa: MATCH_OK
            case UsageRole.CONSTRAINT:
                if self.authority_ref is None:
                    raise PydanticCustomError(
                        "constraint_requires_authority",
                        "constraint entries require trusted authority",
                    )
            case UsageRole.REFERENCE:
                pass
        return self

    @model_validator(mode="after")
    def require_personal_owner(self) -> Self:
        match self.document_kind:
            case MemoryKind.USER:
                if self.scope.kind is not ScopeKind.CHANNEL_MEMBER:
                    raise PydanticCustomError(
                        "user_memory_scope_required",
                        "user entries require channel member ownership",
                    )
            case MemoryKind.TEAM | MemoryKind.CORE | MemoryKind.DAILY | MemoryKind.SOUL:
                if self.scope.kind is ScopeKind.CHANNEL_MEMBER:
                    raise PydanticCustomError(
                        "personal_entry_requires_user_memory",
                        "personal entries belong to user memory",
                    )
        return self

    @model_validator(mode="after")
    def require_document_fields(self) -> Self:
        match self.document_kind:  # noqa: MATCH_OK
            case MemoryKind.SOUL:
                if self.soul_section is None:
                    raise PydanticCustomError(
                        "soul_entry_requires_section",
                        "SOUL entries require an editorial section",
                    )
                match self.status:  # noqa: MATCH_OK
                    case MemoryStatus.ACTIVE:
                        if (
                            self.kind is not MemoryEntryKind.DECISION
                            or self.origin is not MemoryOrigin.DIRECT
                            or self.authority_ref is None
                        ):
                            raise PydanticCustomError(
                                "soul_active_entry_requires_direct_decision",
                                "active SOUL entries require a direct authorized decision",
                            )
                    case MemoryStatus.CONTESTED | MemoryStatus.SUPERSEDED | MemoryStatus.RETRACTED:
                        pass
            case MemoryKind.TEAM | MemoryKind.CORE | MemoryKind.DAILY | MemoryKind.USER:
                if self.soul_section is not None or self.example_refs:
                    raise PydanticCustomError(
                        "non_soul_entry_forbids_soul_fields",
                        "only SOUL entries carry sections and example references",
                    )
        return self


class MemoryRevision(KnowledgeContractModel):
    document_id: BoundedId
    revision_id: BoundedId
    previous_revision_id: BoundedId | None
    body_sha256: Sha256Digest
    entry_ids: Annotated[tuple[BoundedId, ...], Field(max_length=512)] = ()
    created_at: UtcDatetime

    @model_validator(mode="after")
    def require_unique_entries(self) -> Self:
        if len(self.entry_ids) != len(set(self.entry_ids)):
            raise PydanticCustomError(
                "memory_revision_entries_not_unique",
                "memory revision entry IDs must be unique",
            )
        return self
