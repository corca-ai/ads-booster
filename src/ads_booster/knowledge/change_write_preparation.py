from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.knowledge.file_paths import (
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    RevisionFileDraft,
)
from ads_booster.knowledge.repository_types import (
    HeadExpectation,
    MemoryRevisionWrite,
    PageRevisionWrite,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.governance_contracts import ConstraintBinding
    from ads_booster.knowledge.memory import MemorySnapshot
    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository


def prepare_page_write(
    repository: SqliteKnowledgeRepository,
    operation_id: str,
    snapshot: PageSnapshot,
) -> PageRevisionWrite:
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=KnowledgeRevisionTarget(
                page_id=snapshot.page.page_id,
                revision_id=snapshot.revision.revision_id,
            ),
            content=snapshot.body,
            sha256=snapshot.revision.body_sha256,
        )
    )
    return PageRevisionWrite(
        page=snapshot.page,
        revision=snapshot.revision,
        expected=HeadExpectation(
            entity_id=snapshot.page.page_id,
            expected_revision_id=snapshot.revision.previous_revision_id,
            resulting_revision_id=snapshot.revision.revision_id,
        ),
        prepared_file=prepared,
    )


def prepare_memory_write(
    repository: SqliteKnowledgeRepository,
    operation_id: str,
    snapshot: MemorySnapshot,
    constraints: tuple[ConstraintBinding, ...],
) -> MemoryRevisionWrite:
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id=snapshot.document.workspace_id,
                document_id=snapshot.document.document_id,
                revision_id=snapshot.revision.revision_id,
            ),
            content=snapshot.body,
            sha256=snapshot.revision.body_sha256,
        )
    )
    return MemoryRevisionWrite(
        document=snapshot.document,
        revision=snapshot.revision,
        expected=HeadExpectation(
            entity_id=snapshot.document.document_id,
            expected_revision_id=snapshot.revision.previous_revision_id,
            resulting_revision_id=snapshot.revision.revision_id,
        ),
        prepared_file=prepared,
        entries=snapshot.entries,
        constraints=constraints,
    )


__all__ = ["prepare_memory_write", "prepare_page_write"]
