from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import sqlite3
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ads_booster.knowledge.contracts import (
        ActorContext,
        DependencyState,
        Source,
        SourceKind,
    )
    from ads_booster.knowledge.file_store import ImmutableFileStore
    from ads_booster.knowledge.repository_types import (
        RepositoryCommitBoundary,
        SourceAdmissionChange,
        StoredMemory,
        StoredPage,
        StoredSource,
    )


class KnowledgeRepository(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def files(self) -> ImmutableFileStore: ...

    def connection(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def reach_commit_boundary(self, boundary: RepositoryCommitBoundary) -> None: ...

    def read_memory(
        self,
        actor: ActorContext,
        document_id: str,
        revision_id: str | None = None,
    ) -> StoredMemory | None: ...

    def memory_history_redaction(
        self,
        workspace_id: str,
        document_id: str,
        revision_id: str,
    ) -> tuple[str, str | None] | None: ...

    def source_by_identity(
        self,
        actor: ActorContext,
        source_kind: SourceKind,
        source_identity: str,
    ) -> Source | None: ...

    def change_source_admission(self, command: SourceAdmissionChange) -> Source: ...

    def read_source(self, actor: ActorContext, source_id: str) -> StoredSource | None: ...

    def read_page(
        self,
        actor: ActorContext,
        page_id: str,
        revision_id: str | None = None,
    ) -> StoredPage | None: ...

    def page_head(self, actor: ActorContext, page_id: str) -> str | None: ...

    def claim_dependency_state(
        self,
        actor: ActorContext,
        claim_id: str,
        revision_id: str,
    ) -> DependencyState | None: ...


__all__ = ["KnowledgeRepository"]
