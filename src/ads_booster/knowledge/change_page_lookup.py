from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import PageRelationKind, WikiPageStatus

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_types import StoredPage
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.wiki_contracts import WikiPage


class PageSnapshotLike(Protocol):
    @property
    def page(self) -> WikiPage: ...


class PageReader(Protocol):
    def read_page(
        self,
        actor: ActorContext,
        page_id: str,
        revision_id: str | None = None,
    ) -> StoredPage | None: ...


def page_candidates(snapshots: tuple[PageSnapshotLike, ...], *, name: str) -> tuple[str, ...]:
    normalized = name.casefold()
    return tuple(
        snapshot.page.page_id
        for snapshot in snapshots
        if snapshot.page.title.casefold() == normalized
        or any(alias.casefold() == normalized for alias in snapshot.page.aliases)
    )


def read_current_page(
    repository: PageReader,
    actor: ActorContext,
    page_id: str,
) -> StoredPage | None:
    current_id = page_id
    seen: set[str] = set()
    while True:
        if current_id in seen:
            raise ChangeValidationError("redirect_cycle", page_id)
        seen.add(current_id)
        stored = repository.read_page(actor, current_id)
        if stored is None or stored.page.status is not WikiPageStatus.REDIRECT:
            return stored
        targets = tuple(
            relation.to_page_id
            for relation in stored.revision.relations
            if relation.kind is PageRelationKind.SUMMARIZES
        )
        if len(targets) != 1:
            raise ChangeValidationError("redirect_target_invalid", current_id)
        current_id = targets[0]


__all__ = ["PageReader", "page_candidates", "read_current_page"]
