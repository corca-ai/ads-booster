from __future__ import annotations

# ruff: noqa: C901, EM101
from typing import TYPE_CHECKING, Never, assert_never, cast

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.operation_enums import KnowledgeOperationKind

if TYPE_CHECKING:
    from ads_booster.knowledge.operation_contracts import KnowledgeOperation
    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_types import StoredPage
    from ads_booster.knowledge.scope_contracts import ActorContext


def require_page_operation_semantics(
    *,
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    operation: KnowledgeOperation | None,
    pages: tuple[PageSnapshot, ...],
) -> None:
    if operation is None:
        return
    previous = tuple(_previous(repository, actor, snapshot) for snapshot in pages)
    operation_kind = cast("KnowledgeOperationKind | str", operation.kind)
    match operation_kind:
        case KnowledgeOperationKind.PAGE_CREATE:
            if any(item is not None for item in previous):
                raise ChangeValidationError(
                    "page_create_requires_new_identity", operation.operation_id
                )
        case KnowledgeOperationKind.PAGE_RENAME:
            if len(pages) != 1 or previous[0] is None:
                raise ChangeValidationError("page_rename_target_invalid", operation.operation_id)
            _require_rename_only(previous[0], pages[0])
        case KnowledgeOperationKind.PAGE_LINK | KnowledgeOperationKind.PAGE_UNLINK:
            if len(pages) != 1 or previous[0] is None:
                raise ChangeValidationError("page_relation_target_invalid", operation.operation_id)
            _require_nonrelation_content(previous[0], pages[0])
        case (
            KnowledgeOperationKind.PAGE_EDIT
            | KnowledgeOperationKind.CLAIM_ADD
            | KnowledgeOperationKind.CLAIM_UPDATE
            | KnowledgeOperationKind.CLAIM_MERGE
            | KnowledgeOperationKind.CLAIM_SUPERSEDE
            | KnowledgeOperationKind.CLAIM_RETRACT
        ):
            for before, after in zip(previous, pages, strict=True):
                if before is None:
                    raise ChangeValidationError("page_edit_target_missing", after.page.page_id)
                _require_unrelated_claims(before, after, frozenset(operation.claim_ids))
        case KnowledgeOperationKind.PAGE_MERGE | KnowledgeOperationKind.PAGE_SPLIT:
            return
        case _ as unreachable:
            assert_never(cast("Never", unreachable))


def _previous(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    snapshot: PageSnapshot,
) -> StoredPage | None:
    revision_id = snapshot.revision.previous_revision_id
    if revision_id is None:
        return None
    previous = repository.read_page(actor, snapshot.page.page_id, revision_id)
    if previous is None:
        raise ChangeValidationError("previous_page_revision_missing", snapshot.page.page_id)
    return previous


def _require_rename_only(previous: StoredPage, current: PageSnapshot) -> None:
    if (
        current.body != previous.body
        or current.revision.claims != previous.revision.claims
        or current.revision.relations != previous.revision.relations
        or current.page.attributes != previous.page.attributes
        or current.page.scope != previous.page.scope
        or current.page.status != previous.page.status
        or previous.page.title not in current.page.aliases
    ):
        raise ChangeValidationError("page_rename_changed_content", current.page.page_id)


def _require_nonrelation_content(previous: StoredPage, current: PageSnapshot) -> None:
    if (
        current.body != previous.body
        or current.revision.claims != previous.revision.claims
        or current.page.title != previous.page.title
        or current.page.aliases != previous.page.aliases
        or current.page.attributes != previous.page.attributes
        or current.page.scope != previous.page.scope
        or current.page.status != previous.page.status
    ):
        raise ChangeValidationError("page_relation_changed_content", current.page.page_id)


def _require_unrelated_claims(
    previous: StoredPage,
    current: PageSnapshot,
    affected_ids: frozenset[str],
) -> None:
    before = {claim.claim_id: claim for claim in previous.revision.claims}
    after = {claim.claim_id: claim for claim in current.revision.claims}
    for claim_id, claim in before.items():
        if claim_id not in affected_ids and after.get(claim_id) != claim:
            raise ChangeValidationError("unrelated_claim_changed", claim_id)


__all__ = ["require_page_operation_semantics"]
