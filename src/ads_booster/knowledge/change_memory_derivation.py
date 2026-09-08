from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    claim_semantic_fingerprint,
)
from ads_booster.knowledge.contract_types import (
    DependencyState,
    EvidenceKind,
    InstructionAuthority,
    MemoryEntryKind,
    MemoryOrigin,
    Provenance,
)
from ads_booster.knowledge.evidence_contracts import EvidenceRef

if TYPE_CHECKING:
    from ads_booster.knowledge.memory_contracts import MemoryEntry, WikiSummaryRef
    from ads_booster.knowledge.wiki_contracts import Claim


class WikiClaimLike(Protocol):
    @property
    def page_id(self) -> str: ...

    @property
    def claim(self) -> Claim: ...


class MemorySnapshotLike(Protocol):
    @property
    def entries(self) -> tuple[MemoryEntry, ...]: ...


def handover_direct_entry_to_wiki(
    *, entry: MemoryEntry, wiki_ref: WikiSummaryRef, text: str
) -> MemoryEntry:
    if entry.origin is not MemoryOrigin.DIRECT:
        raise ChangeValidationError("handover_requires_direct_entry", entry.entry_id)
    claim_ref = EvidenceRef(
        evidence_kind=EvidenceKind.CLAIM,
        evidence_id=wiki_ref.claim_id,
        revision_id=wiki_ref.revision_id,
        scope=entry.scope,
        instruction_authority=InstructionAuthority.DATA,
        provenance=Provenance.AGENT_DERIVED,
    )
    return entry.model_copy(
        update={
            "text": text,
            "kind": MemoryEntryKind.FACT,
            "origin": MemoryOrigin.WIKI_SUMMARY,
            "source_refs": (claim_ref,),
            "wiki_ref": wiki_ref,
            "authority_ref": None,
        }
    )


def invalidate_wiki_dependencies(
    *,
    entries: tuple[MemoryEntry, ...],
    current_claims: tuple[WikiClaimLike, ...],
    restricted_claim_ids: frozenset[str] = frozenset(),
) -> tuple[MemoryEntry, ...]:
    current = {(record.page_id, record.claim.claim_id): record for record in current_claims}
    invalidated: list[MemoryEntry] = []
    for entry in entries:
        wiki_ref = entry.wiki_ref
        if wiki_ref is None:
            invalidated.append(entry)
            continue
        record = current.get((wiki_ref.page_id, wiki_ref.claim_id))
        if wiki_ref.claim_id in restricted_claim_ids or record is None:
            state = DependencyState.RESTRICTED
        elif claim_semantic_fingerprint(record.claim) != wiki_ref.semantic_fingerprint:
            state = DependencyState.STALE
        else:
            state = DependencyState.CURRENT
        invalidated.append(entry.model_copy(update={"dependency_state": state}))
    return tuple(invalidated)


def visible_memory_entries(snapshot: MemorySnapshotLike) -> tuple[MemoryEntry, ...]:
    return tuple(
        entry
        for entry in snapshot.entries
        if entry.dependency_state is DependencyState.CURRENT and entry.status.value == "active"
    )


__all__ = [
    "handover_direct_entry_to_wiki",
    "invalidate_wiki_dependencies",
    "visible_memory_entries",
]
