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
    ClaimKind,
    ClaimStatus,
    KnowledgeContractModel,
    PageRelationKind,
    UtcDatetime,
    WikiPageStatus,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.scope_contracts import AccessScope


class Applicability(KnowledgeContractModel):
    product_refs: Annotated[tuple[BoundedId, ...], Field(max_length=32)] = ()
    markets: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    channels: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    effective_from: date | None = None
    effective_until: date | None = None

    @model_validator(mode="after")
    def require_effective_period(self) -> Self:
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise PydanticCustomError(
                "applicability_period_not_ordered",
                "applicability end date must not precede its start date",
            )
        return self


class PageAttribute(KnowledgeContractModel):
    key: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[str, Field(min_length=1, max_length=500)]


class Claim(KnowledgeContractModel):
    claim_id: BoundedId
    kind: ClaimKind
    statement: BoundedText
    applicability: Applicability | None = None
    status: ClaimStatus
    evidence_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1, max_length=128)]
    counter_evidence_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()
    authority_ref: AuthorityRef | None = None
    admission_reason: BoundedReason
    observed_at: UtcDatetime
    reviewed_at: UtcDatetime | None = None
    review_after: UtcDatetime | None = None
    review_trigger: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def require_kind_authority(self) -> Self:
        if self.kind is ClaimKind.DECISION and self.authority_ref is None:
            raise PydanticCustomError(
                "decision_requires_authority",
                "decision claims require an authenticated authority reference",
            )
        if self.kind is not ClaimKind.DECISION and self.authority_ref is not None:
            raise PydanticCustomError(
                "non_decision_forbids_authority",
                "only decision claims carry decision authority",
            )
        return self


class PageRelation(KnowledgeContractModel):
    relation_id: BoundedId
    from_page_id: BoundedId
    to_page_id: BoundedId
    kind: PageRelationKind
    reason: BoundedReason
    claim_refs: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()
    evidence_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=64)] = ()


class EvidenceEdge(KnowledgeContractModel):
    derived_evidence_id: BoundedId
    upstream_evidence_id: BoundedId


class WikiPage(KnowledgeContractModel):
    page_id: BoundedId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    aliases: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    summary: Annotated[str, Field(max_length=4_000)] = ""
    attributes: Annotated[tuple[PageAttribute, ...], Field(max_length=64)] = ()
    scope: AccessScope
    current_revision_id: BoundedId
    status: WikiPageStatus

    @model_validator(mode="after")
    def require_unique_aliases(self) -> Self:
        if len(set(self.aliases)) != len(self.aliases):
            raise PydanticCustomError(
                "page_aliases_not_unique",
                "page aliases must be unique",
            )
        return self


class KnowledgeRevision(KnowledgeContractModel):
    page_id: BoundedId
    revision_id: BoundedId
    previous_revision_id: BoundedId | None
    body_sha256: Sha256Digest
    title: Annotated[str, Field(min_length=1, max_length=300)]
    aliases: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    attributes: Annotated[tuple[PageAttribute, ...], Field(max_length=64)] = ()
    claims: Annotated[tuple[Claim, ...], Field(max_length=256)] = ()
    relations: Annotated[tuple[PageRelation, ...], Field(max_length=256)] = ()
    evidence_ancestry: Annotated[tuple[EvidenceEdge, ...], Field(max_length=512)] = ()
    scope: AccessScope

    @model_validator(mode="after")
    def require_unique_revision_members(self) -> Self:
        claim_ids = tuple(item.claim_id for item in self.claims)
        relation_ids = tuple(item.relation_id for item in self.relations)
        if len(claim_ids) != len(set(claim_ids)):
            raise PydanticCustomError(
                "revision_claims_not_unique",
                "revision claim IDs must be unique",
            )
        if len(relation_ids) != len(set(relation_ids)):
            raise PydanticCustomError(
                "revision_relations_not_unique",
                "revision relation IDs must be unique",
            )
        if any(item.from_page_id != self.page_id for item in self.relations):
            raise PydanticCustomError(
                "revision_relation_page_mismatch",
                "revision relations must originate from the revision page",
            )
        return self
