from __future__ import annotations

# ruff: noqa: EM101, PLR0913
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    claim_semantic_fingerprint,
)
from ads_booster.knowledge.contract_types import WikiPageStatus
from ads_booster.knowledge.memory import (
    MemorySnapshot,
    WikiClaimRecord,
    invalidate_wiki_dependencies,
)
from ads_booster.knowledge.memory_contracts import MemoryRevision
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import MemoryOperationKind

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext


@dataclass(frozen=True, slots=True)
class DerivedMemoryInvalidation:
    snapshot: MemorySnapshot
    operation: MemoryOperation


def derive_memory_invalidations(
    *,
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    pages: tuple[PageSnapshot, ...],
    operation_id: str,
    at: datetime,
    excluded_document_ids: frozenset[str],
) -> tuple[DerivedMemoryInvalidation, ...]:
    semantic_ids: set[str] = set()
    restricted_ids: set[str] = set()
    current_records: list[WikiClaimRecord] = []
    for snapshot in pages:
        previous_id = snapshot.revision.previous_revision_id
        if previous_id is None:
            continue
        previous = repository.read_page(actor, snapshot.page.page_id, previous_id)
        if previous is None:
            raise ChangeValidationError("previous_page_revision_missing", snapshot.page.page_id)
        previous_claims = {claim.claim_id: claim for claim in previous.revision.claims}
        current_claims = {claim.claim_id: claim for claim in snapshot.revision.claims}
        current_records.extend(
            WikiClaimRecord(
                page_id=snapshot.page.page_id,
                revision_id=snapshot.revision.revision_id,
                claim=claim,
            )
            for claim in snapshot.revision.claims
        )
        removed = previous_claims.keys() - current_claims.keys()
        restricted_ids.update(removed)
        if (
            snapshot.page.status is WikiPageStatus.RETRACTED
            or snapshot.page.scope != previous.page.scope
        ):
            restricted_ids.update(previous_claims)
            continue
        semantic_ids.update(
            claim_id
            for claim_id in previous_claims.keys() & current_claims.keys()
            if claim_semantic_fingerprint(previous_claims[claim_id])
            != claim_semantic_fingerprint(current_claims[claim_id])
        )
    affected_ids = tuple(sorted(semantic_ids | restricted_ids))
    if not affected_ids:
        return ()
    dependents = repository.memory_dependents(actor, affected_ids)
    results: list[DerivedMemoryInvalidation] = []
    for stored in dependents:
        if stored.document.document_id in excluded_document_ids:
            raise ChangeValidationError(
                "dependency_publication_overlap", stored.document.document_id
            )
        entries = invalidate_wiki_dependencies(
            entries=stored.entries,
            current_claims=tuple(current_records),
            restricted_claim_ids=frozenset(restricted_ids),
        )
        if entries == stored.entries:
            continue
        revision_id = (
            f"{stored.document.document_id}.invalidate."
            f"{sha256(operation_id.encode()).hexdigest()[:16]}"
        )
        revision = MemoryRevision(
            document_id=stored.document.document_id,
            revision_id=revision_id,
            previous_revision_id=stored.revision.revision_id,
            body_sha256=stored.revision.body_sha256,
            entry_ids=stored.revision.entry_ids,
            created_at=at,
        )
        snapshot = MemorySnapshot(
            document=stored.document.model_copy(update={"head_revision_id": revision_id}),
            revision=revision,
            entries=entries,
            body=stored.body,
        )
        changed_entry = next(
            entry
            for entry, previous_entry in zip(entries, stored.entries, strict=True)
            if entry != previous_entry
        )
        operation = MemoryOperation(
            operation_id=operation_id,
            kind=MemoryOperationKind.UPDATE,
            document_id=stored.document.document_id,
            entry_id=changed_entry.entry_id,
            expected_revision_id=stored.revision.revision_id,
            reason="Invalidate a derived memory after its Wiki dependency changed.",
            evidence_refs=affected_ids,
        )
        results.append(DerivedMemoryInvalidation(snapshot=snapshot, operation=operation))
    return tuple(results)


__all__ = ["DerivedMemoryInvalidation", "derive_memory_invalidations"]
