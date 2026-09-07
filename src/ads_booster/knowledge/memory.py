from __future__ import annotations

# ruff: noqa: D105, EM101, PLR0913, TC001
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Never, assert_never, cast

from ads_booster.knowledge.change_memory_derivation import (
    handover_direct_entry_to_wiki,
    invalidate_wiki_dependencies,
    visible_memory_entries,
)
from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    EvidenceRecord,
    claim_semantic_fingerprint,
    require_acyclic_ancestry,
    require_scope_not_wider,
)
from ads_booster.knowledge.contract_types import (
    GrantCapability,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    Provenance,
    ScopeKind,
)
from ads_booster.knowledge.errors import EvidenceResolutionError, KnowledgePolicyError
from ads_booster.knowledge.evidence_contracts import AuthenticatedEvent, EvidenceRef
from ads_booster.knowledge.governance_contracts import BrandTarget
from ads_booster.knowledge.grant_policy import (
    authorize_brand_voice_edit,
    authorize_write,
    intersect_lineage_scopes,
)
from ads_booster.knowledge.memory_contracts import (
    MemoryDocument,
    MemoryEntry,
    MemoryRevision,
)
from ads_booster.knowledge.policy import MemoryAuthorityContext, require_memory_authority
from ads_booster.knowledge.wiki_contracts import Claim

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.scope_contracts import ActorContext


@dataclass(frozen=True, slots=True)
class WikiClaimRecord:
    page_id: str
    revision_id: str
    claim: Claim


@dataclass(frozen=True, slots=True)
class ValidationCatalog:
    evidence: tuple[EvidenceRecord, ...]
    events: tuple[AuthenticatedEvent, ...]
    wiki_claims: tuple[WikiClaimRecord, ...]


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    document: MemoryDocument
    revision: MemoryRevision
    entries: tuple[MemoryEntry, ...]
    body: bytes

    def __post_init__(self) -> None:
        if (
            self.document.document_id != self.revision.document_id
            or self.document.head_revision_id != self.revision.revision_id
        ):
            raise ChangeValidationError("memory_head_mismatch", self.document.document_id)
        if sha256(self.body).hexdigest() != self.revision.body_sha256:
            raise ChangeValidationError("memory_body_digest_mismatch", self.document.document_id)
        if tuple(entry.entry_id for entry in self.entries) != self.revision.entry_ids:
            raise ChangeValidationError("memory_entry_manifest_mismatch", self.document.document_id)


def validate_memory_change(
    *,
    snapshot: MemorySnapshot,
    entries: tuple[MemoryEntry, ...],
    body: bytes,
    revision_id: str,
    actor: ActorContext,
    catalog: ValidationCatalog,
    at: datetime,
) -> MemorySnapshot:
    target_scope = snapshot.entries[0].scope if snapshot.entries else actor.conversation_scope
    if target_scope.kind is ScopeKind.MEMBER:
        raise ChangeValidationError(
            "shared_memory_requires_workspace_scope", snapshot.document.document_id
        )
    try:
        _ = authorize_write(actor=actor, target_scope=target_scope, at=at)
        if snapshot.document.kind is MemoryKind.SOUL:
            brand_id = snapshot.document.brand_id
            if brand_id is None:
                raise ChangeValidationError("invalid_memory_brand", snapshot.document.document_id)
            try:
                _ = authorize_brand_voice_edit(
                    actor=actor,
                    target=BrandTarget(scope=target_scope, brand_id=brand_id),
                    at=at,
                )
            except KnowledgePolicyError as error:
                raise ChangeValidationError(
                    "soul_authority_required", snapshot.document.document_id
                ) from error
    except KnowledgePolicyError as error:
        raise ChangeValidationError(error.code, snapshot.document.document_id) from error

    if len({entry.entry_id for entry in entries}) != len(entries):
        raise ChangeValidationError("memory_entries_not_unique", snapshot.document.document_id)
    for entry in entries:
        _validate_entry(snapshot.document, entry, actor, catalog)

    revision = MemoryRevision(
        document_id=snapshot.document.document_id,
        revision_id=revision_id,
        previous_revision_id=snapshot.revision.revision_id,
        body_sha256=sha256(body).hexdigest(),
        entry_ids=tuple(entry.entry_id for entry in entries),
        created_at=at,
    )
    document = snapshot.document.model_copy(update={"head_revision_id": revision_id})
    return MemorySnapshot(document=document, revision=revision, entries=entries, body=body)


def validate_memory_snapshot(
    *,
    snapshot: MemorySnapshot,
    actor: ActorContext,
    catalog: ValidationCatalog,
    at: datetime,
) -> None:
    target_scope = snapshot.entries[0].scope if snapshot.entries else actor.conversation_scope
    if target_scope.kind is ScopeKind.MEMBER:
        raise ChangeValidationError(
            "shared_memory_requires_workspace_scope", snapshot.document.document_id
        )
    try:
        _ = authorize_write(actor=actor, target_scope=target_scope, at=at)
        if snapshot.document.kind is MemoryKind.SOUL:
            brand_id = snapshot.document.brand_id
            if brand_id is None:
                raise ChangeValidationError("invalid_memory_brand", snapshot.document.document_id)
            _ = authorize_brand_voice_edit(
                actor=actor,
                target=BrandTarget(scope=target_scope, brand_id=brand_id),
                at=at,
            )
    except KnowledgePolicyError as error:
        code = (
            "soul_authority_required" if snapshot.document.kind is MemoryKind.SOUL else error.code
        )
        raise ChangeValidationError(code, snapshot.document.document_id) from error
    for entry in snapshot.entries:
        _validate_entry(snapshot.document, entry, actor, catalog)


def _validate_entry(
    document: MemoryDocument,
    entry: MemoryEntry,
    actor: ActorContext,
    catalog: ValidationCatalog,
) -> None:
    if entry.document_id != document.document_id or entry.document_kind is not document.kind:
        raise ChangeValidationError("memory_entry_document_mismatch", entry.entry_id)
    if entry.scope.kind is ScopeKind.MEMBER:
        raise ChangeValidationError("scope_expansion_forbidden", entry.entry_id)
    if entry.scope.workspace_id != document.workspace_id:
        raise ChangeValidationError("workspace_scope_mismatch", entry.entry_id)
    records = tuple(_resolve_evidence(ref, catalog) for ref in entry.source_refs)
    scopes = tuple(record.ref.scope for record in records)
    lineage_scope = intersect_lineage_scopes(scopes)
    require_scope_not_wider(source=lineage_scope, target=entry.scope, target_id=entry.entry_id)
    for record in records:
        require_acyclic_ancestry(record.ancestry)
    if entry.kind is MemoryEntryKind.DECISION or entry.authority_ref is not None:
        try:
            _ = require_memory_authority(
                entry=entry,
                context=MemoryAuthorityContext(
                    document=document, actor=actor, events=catalog.events
                ),
            )
        except KnowledgePolicyError as error:
            raise ChangeValidationError(error.code, entry.entry_id) from error
        _require_decision_event(entry, catalog)
    origin_value = cast("MemoryOrigin | str", entry.origin)
    match origin_value:
        case MemoryOrigin.DIRECT:
            return
        case MemoryOrigin.WIKI_SUMMARY:
            _require_current_wiki_summary(entry, catalog)
        case _ as unreachable:
            assert_never(cast("Never", unreachable))


def _resolve_evidence(ref: EvidenceRef, catalog: ValidationCatalog) -> EvidenceRecord:
    record = next(
        (
            item
            for item in catalog.evidence
            if (
                item.ref.evidence_kind,
                item.ref.evidence_id,
                item.ref.revision_id,
                item.ref.segment_id,
            )
            == (ref.evidence_kind, ref.evidence_id, ref.revision_id, ref.segment_id)
        ),
        None,
    )
    if record is None:
        raise EvidenceResolutionError(evidence_id=ref.evidence_id, revision_id=ref.revision_id)
    if record.ref != ref:
        raise ChangeValidationError("evidence_pointer_mismatch", ref.evidence_id)
    if ref.quote_sha256 is not None and (
        record.quote is None or sha256(record.quote.encode()).hexdigest() != ref.quote_sha256
    ):
        raise ChangeValidationError("evidence_quote_mismatch", ref.evidence_id)
    return record


def _require_decision_event(entry: MemoryEntry, catalog: ValidationCatalog) -> None:
    authority = entry.authority_ref
    if authority is None:
        raise ChangeValidationError("decision_requires_authority", entry.entry_id)
    event = next((item for item in catalog.events if item.event_id == authority.event_id), None)
    if event is None or event.provenance is not Provenance.HUMAN_DIRECT:
        raise ChangeValidationError("decision_requires_human_direct_event", entry.entry_id)
    if (
        entry.document_kind is MemoryKind.SOUL
        and GrantCapability.BRAND_VOICE_EDIT not in event.capabilities
    ):
        raise ChangeValidationError("soul_authority_required", entry.entry_id)


def _require_current_wiki_summary(entry: MemoryEntry, catalog: ValidationCatalog) -> None:
    wiki_ref = entry.wiki_ref
    if wiki_ref is None:
        raise ChangeValidationError("wiki_summary_requires_ref", entry.entry_id)
    record = next(
        (
            item
            for item in catalog.wiki_claims
            if item.page_id == wiki_ref.page_id
            and item.revision_id == wiki_ref.revision_id
            and item.claim.claim_id == wiki_ref.claim_id
        ),
        None,
    )
    if record is None:
        raise ChangeValidationError("wiki_summary_source_not_found", entry.entry_id)
    if claim_semantic_fingerprint(record.claim) != wiki_ref.semantic_fingerprint:
        raise ChangeValidationError("wiki_summary_stale", entry.entry_id)


__all__ = [
    "MemorySnapshot",
    "ValidationCatalog",
    "WikiClaimRecord",
    "handover_direct_entry_to_wiki",
    "invalidate_wiki_dependencies",
    "validate_memory_change",
    "validate_memory_snapshot",
    "visible_memory_entries",
]
