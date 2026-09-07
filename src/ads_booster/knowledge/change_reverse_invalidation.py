from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import DependencyState, MemoryKind, MemoryStatus
from ads_booster.knowledge.repository_types import EvidenceDependencyInvalidation

if TYPE_CHECKING:
    from ads_booster.knowledge.memory import MemorySnapshot
    from ads_booster.knowledge.memory_contracts import MemoryEntry
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext


class MemoryPublicationLike(Protocol):
    @property
    def snapshot(self) -> MemorySnapshot: ...


def derive_wiki_dependency_invalidations(
    *,
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    memories: tuple[MemoryPublicationLike, ...],
) -> tuple[EvidenceDependencyInvalidation, ...]:
    results: list[EvidenceDependencyInvalidation] = []
    for publication in memories:
        snapshot = publication.snapshot
        previous_revision_id = snapshot.revision.previous_revision_id
        if snapshot.document.kind is MemoryKind.SOUL or previous_revision_id is None:
            continue
        previous = repository.read_memory(
            actor,
            snapshot.document.document_id,
            previous_revision_id,
        )
        if previous is None:
            raise ChangeValidationError(
                "previous_memory_revision_missing", snapshot.document.document_id
            )
        current = {entry.entry_id: entry for entry in snapshot.entries}
        for entry in previous.entries:
            state = _dependency_state(entry, current.get(entry.entry_id))
            if state is None:
                continue
            results.append(
                EvidenceDependencyInvalidation(
                    upstream_kind="memory_entry",
                    upstream_id=entry.entry_id,
                    upstream_revision_id=previous_revision_id,
                    resulting_state=state,
                    reason="Canonical memory evidence changed before Wiki refresh.",
                )
            )
    keys = {
        (item.upstream_id, item.upstream_revision_id, item.resulting_state) for item in results
    }
    if len(keys) != len(results):
        raise ChangeValidationError("dependency_invalidations_not_unique")
    return tuple(results)


def _dependency_state(
    previous: MemoryEntry,
    current: MemoryEntry | None,
) -> DependencyState | None:
    if (
        current is None
        or current.status is not MemoryStatus.ACTIVE
        or current.scope != previous.scope
    ):
        return DependencyState.RESTRICTED
    comparable = previous.model_copy(update={"dependency_state": current.dependency_state})
    if comparable != current:
        return DependencyState.STALE
    return None


__all__ = ["derive_wiki_dependency_invalidations"]
