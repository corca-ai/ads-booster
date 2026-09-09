from __future__ import annotations

# ruff: noqa: D107, EM101, TC001
from hashlib import sha256
from typing import TYPE_CHECKING, Never, assert_never, cast

from ads_booster.knowledge.change_evidence_overlay import PlannedEvidenceOverlay
from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    EvidenceRecord,
    require_scope_not_wider,
)
from ads_booster.knowledge.contract_types import (
    ConversationRole,
    InstructionAuthority,
    Provenance,
)
from ads_booster.knowledge.evidence_contracts import (
    AuthenticatedEvent,
    AuthorityRef,
    EvidenceRef,
)
from ads_booster.knowledge.grant_policy import authorize_write, intersect_lineage_scopes
from ads_booster.knowledge.memory import MemorySnapshot, ValidationCatalog, WikiClaimRecord
from ads_booster.knowledge.memory_contracts import MemoryEntry
from ads_booster.knowledge.policy import require_claim_authority
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.source_contracts import ConversationEvent, SourceSegment
from ads_booster.knowledge.wiki_contracts import Claim

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository_resolution import ResolvedEvidence
    from ads_booster.knowledge.scope_contracts import ActorContext


class RepositoryEvidenceResolver:
    def __init__(
        self,
        repository: SqliteKnowledgeRepository,
        *,
        pages: tuple[PageSnapshot, ...] = (),
        memories: tuple[MemorySnapshot, ...] = (),
    ) -> None:
        self._repository: SqliteKnowledgeRepository = repository
        self._overlay: PlannedEvidenceOverlay = PlannedEvidenceOverlay(pages, memories)

    def validation_catalog(
        self,
        *,
        actor: ActorContext,
        entries: tuple[MemoryEntry, ...],
        brand_id: str | None,
    ) -> ValidationCatalog:
        refs = tuple(ref for entry in entries for ref in entry.source_refs)
        records = tuple(self._resolve(actor, ref) for ref in refs)
        events = tuple(
            self._authenticated_event(actor, entry, records, brand_id)
            for entry in entries
            if entry.authority_ref is not None
        )
        wiki_claims = tuple(
            self._wiki_claim_record(actor, entry) for entry in entries if entry.wiki_ref is not None
        )
        return ValidationCatalog(evidence=records, events=events, wiki_claims=wiki_claims)

    def validate_references(
        self,
        actor: ActorContext,
        references: tuple[EvidenceRef, ...],
    ) -> tuple[EvidenceRecord, ...]:
        """Resolve and authenticate exact evidence pointers without changing them."""
        return tuple(self._resolve(actor, reference) for reference in references)

    def validate_page(self, actor: ActorContext, snapshot: PageSnapshot, at: datetime) -> None:
        _ = authorize_write(actor=actor, target_scope=snapshot.page.scope, at=at)
        for claim in snapshot.revision.claims:
            records = tuple(
                self._resolve(actor, ref)
                for ref in (*claim.evidence_refs, *claim.counter_evidence_refs)
            )
            lineage = intersect_lineage_scopes(tuple(item.ref.scope for item in records))
            require_scope_not_wider(
                source=lineage,
                target=snapshot.page.scope,
                target_id=claim.claim_id,
            )
            event = self._claim_event(actor, claim, records)
            _ = require_claim_authority(
                claim=claim,
                actor=actor,
                events=() if event is None else (event,),
            )

    def _resolve(self, actor: ActorContext, ref: EvidenceRef) -> EvidenceRecord:
        resolved = self._resolve_value(actor, ref)
        resolved_value = cast("ResolvedEvidence | str", resolved)
        match resolved_value:
            case ConversationEvent():
                authority = (
                    InstructionAuthority.AUTHORIZED_USER
                    if resolved_value.role is ConversationRole.USER
                    else InstructionAuthority.DATA
                )
                provenance = (
                    Provenance.HUMAN_DIRECT
                    if resolved_value.role is ConversationRole.USER
                    else Provenance.AGENT_DERIVED
                )
                quote = resolved_value.text
                scope = resolved_value.scope
            case SourceSegment():
                source = RepositoryToolState(self._repository).read_source_extract(
                    actor, resolved_value.source_id, resolved_value.revision_id
                )
                if source is None:
                    raise ChangeValidationError("evidence_source_missing", resolved_value.source_id)
                body = source.body.decode("utf-8")
                if resolved_value.quote_range.end > len(body):
                    raise ChangeValidationError("evidence_pointer_mismatch", ref.evidence_id)
                quote = body[resolved_value.quote_range.start : resolved_value.quote_range.end]
                if sha256(
                    quote.encode()
                ).hexdigest() != resolved_value.content_sha256 or ref.segment_id not in (
                    None,
                    resolved_value.segment_id,
                ):
                    raise ChangeValidationError("evidence_pointer_mismatch", ref.evidence_id)
                scope = source.source.scope
                authority = InstructionAuthority.DATA
                provenance = Provenance.EXTERNAL
            case Claim():
                quote = None
                scope = intersect_lineage_scopes(
                    tuple(item.scope for item in resolved_value.evidence_refs)
                )
                authority = InstructionAuthority.DATA
                provenance = Provenance.AGENT_DERIVED
            case MemoryEntry():
                quote = None
                scope = resolved_value.scope
                authority = InstructionAuthority.DATA
                provenance = Provenance.AGENT_DERIVED
            case _ as unreachable:
                assert_never(cast("Never", unreachable))
        actual = ref.model_copy(
            update={
                "quote_sha256": None if quote is None else sha256(quote.encode()).hexdigest(),
                "scope": scope,
                "instruction_authority": authority,
                "provenance": provenance,
            }
        )
        if actual != ref:
            raise ChangeValidationError("evidence_pointer_mismatch", ref.evidence_id)
        return EvidenceRecord(ref=actual, quote=quote)

    def _wiki_claim_record(self, actor: ActorContext, entry: MemoryEntry) -> WikiClaimRecord:
        wiki_ref = entry.wiki_ref
        if wiki_ref is None:
            raise ChangeValidationError("wiki_summary_requires_ref", entry.entry_id)
        source_ref = next(
            (
                ref
                for ref in entry.source_refs
                if ref.evidence_id == wiki_ref.claim_id and ref.revision_id == wiki_ref.revision_id
            ),
            None,
        )
        if source_ref is None:
            raise ChangeValidationError("wiki_summary_source_not_found", entry.entry_id)
        resolved = self._resolve_value(actor, source_ref)
        resolved_value = cast("ResolvedEvidence | str", resolved)
        match resolved_value:
            case Claim():
                return WikiClaimRecord(
                    page_id=wiki_ref.page_id,
                    revision_id=wiki_ref.revision_id,
                    claim=resolved_value,
                )
            case ConversationEvent() | SourceSegment() | MemoryEntry():
                raise ChangeValidationError("wiki_summary_source_not_claim", entry.entry_id)
            case _ as unreachable:
                assert_never(cast("Never", unreachable))

    def _authenticated_event(
        self,
        actor: ActorContext,
        entry: MemoryEntry,
        records: tuple[EvidenceRecord, ...],
        brand_id: str | None,
    ) -> AuthenticatedEvent:
        authority = entry.authority_ref
        if authority is None:
            raise ChangeValidationError("memory_authority_required", entry.entry_id)
        record = next(
            (item for item in records if item.ref.evidence_id == authority.event_id), None
        )
        if record is None:
            raise ChangeValidationError("decision_evidence_not_authoritative", entry.entry_id)
        return self._canonical_event(actor, record.ref, authority, brand_id)

    def _claim_event(
        self,
        actor: ActorContext,
        claim: Claim,
        records: tuple[EvidenceRecord, ...],
    ) -> AuthenticatedEvent | None:
        authority = claim.authority_ref
        if authority is None:
            return None
        record = next(
            (item for item in records if item.ref.evidence_id == authority.event_id), None
        )
        if record is None:
            raise ChangeValidationError("decision_evidence_not_authoritative", claim.claim_id)
        return self._canonical_event(actor, record.ref, authority, None)

    def _canonical_event(
        self,
        actor: ActorContext,
        ref: EvidenceRef,
        authority: AuthorityRef,
        brand_id: str | None,
    ) -> AuthenticatedEvent:
        resolved = self._resolve_value(actor, ref)
        resolved_value = cast("ResolvedEvidence | str", resolved)
        match resolved_value:
            case ConversationEvent():
                if (
                    resolved_value.role is not ConversationRole.USER
                    or resolved_value.message_id != authority.event_id
                    or resolved_value.speaker_ref != authority.actor_ref
                    or resolved_value.scope != authority.scope
                    or resolved_value.scope.workspace_id != authority.workspace_id
                ):
                    raise ChangeValidationError(
                        "decision_authority_binding_mismatch", authority.event_id
                    )
                capabilities = tuple(
                    grant.capability
                    for grant in actor.grants
                    if grant.scope == resolved_value.scope
                    and grant.policy_epoch == actor.policy_epoch
                    and (grant.brand_id is None or grant.brand_id == brand_id)
                )
                return AuthenticatedEvent(
                    event_id=authority.event_id,
                    actor_ref=resolved_value.speaker_ref,
                    workspace_id=resolved_value.scope.workspace_id,
                    scope=resolved_value.scope,
                    provenance=Provenance.HUMAN_DIRECT,
                    authority_class=authority.authority_class,
                    capabilities=capabilities,
                    policy_epoch=authority.policy_epoch,
                    occurred_at=resolved_value.created_at,
                )
            case Claim() | SourceSegment() | MemoryEntry():
                raise ChangeValidationError(
                    "decision_requires_human_direct_event", authority.event_id
                )
            case _ as unreachable:
                assert_never(cast("Never", unreachable))

    def _resolve_value(self, actor: ActorContext, ref: EvidenceRef) -> ResolvedEvidence:
        planned = self._overlay.resolve(ref)
        return planned if planned is not None else self._repository.resolve_evidence(actor, ref)


__all__ = ["RepositoryEvidenceResolver"]
