from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.change_validation import ChangeValidationError

if TYPE_CHECKING:
    from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryRevision
    from ads_booster.knowledge.operation_contracts import KnowledgeOperation, MemoryOperation
    from ads_booster.knowledge.skill_contracts import SkillOperation
    from ads_booster.knowledge.wiki_contracts import KnowledgeRevision, WikiPage


class ChangeGroupLike(Protocol):
    @property
    def operation_id(self) -> str: ...

    @property
    def page_operation(self) -> KnowledgeOperation | None: ...

    @property
    def memory_operations(self) -> tuple[MemoryOperation, ...]: ...

    @property
    def skill_operations(self) -> tuple[SkillOperation, ...]: ...


class PageSnapshotLike(Protocol):
    @property
    def page(self) -> WikiPage: ...

    @property
    def revision(self) -> KnowledgeRevision: ...


class MemorySnapshotLike(Protocol):
    @property
    def document(self) -> MemoryDocument: ...

    @property
    def revision(self) -> MemoryRevision: ...


class MemoryPublicationLike(Protocol):
    @property
    def snapshot(self) -> MemorySnapshotLike: ...


def require_group_bindings(
    group: ChangeGroupLike,
    pages: tuple[PageSnapshotLike, ...],
    memories: tuple[MemoryPublicationLike, ...],
) -> None:
    records = (
        (() if group.page_operation is None else (group.page_operation,))
        + tuple(group.memory_operations)
        + tuple(group.skill_operations)
    )
    if not records or any(record.operation_id != group.operation_id for record in records):
        raise ChangeValidationError("operation_group_binding_mismatch", group.operation_id)
    if group.page_operation is None:
        if pages:
            raise ChangeValidationError("page_operation_missing", group.operation_id)
    else:
        expected = dict(
            zip(
                group.page_operation.target_page_ids,
                group.page_operation.expected_revision_ids,
                strict=True,
            )
        )
        actual = {page.page.page_id: page.revision.previous_revision_id or "none" for page in pages}
        if expected != actual:
            raise ChangeValidationError("page_expected_revision_mismatch", group.operation_id)
    memory_by_document = {operation.document_id: operation for operation in group.memory_operations}
    if len(memory_by_document) != len(group.memory_operations):
        raise ChangeValidationError("memory_operations_not_unique", group.operation_id)
    if set(memory_by_document) != {
        publication.snapshot.document.document_id for publication in memories
    }:
        raise ChangeValidationError("memory_operation_mapping_incomplete", group.operation_id)
    for publication in memories:
        snapshot = publication.snapshot
        operation = memory_by_document[snapshot.document.document_id]
        if operation.expected_revision_id != (snapshot.revision.previous_revision_id or "none"):
            raise ChangeValidationError(
                "memory_expected_revision_mismatch", snapshot.document.document_id
            )


__all__ = ["require_group_bindings"]
