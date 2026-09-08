from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ads_booster.knowledge.memory import MemorySnapshot
    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext


class MemoryPublicationLike(Protocol):
    @property
    def snapshot(self) -> MemorySnapshot: ...


def require_current_actor(
    *,
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    pages: tuple[PageSnapshot, ...],
    memories: tuple[MemoryPublicationLike, ...],
) -> None:
    for snapshot in pages:
        _ = repository.read_page(actor, snapshot.page.page_id)
    for publication in memories:
        _ = repository.read_memory(actor, publication.snapshot.document.document_id)


__all__ = ["require_current_actor"]
