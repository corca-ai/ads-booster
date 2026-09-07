from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    KnowledgeRevision,
    MemoryEntry,
    Source,
    SourceSegment,
    WikiPage,
)
from ads_booster.knowledge.file_paths import (
    KnowledgeFileStoreError,
    KnowledgeRevisionTarget,
    SourceFileKind,
    SourceRevisionTarget,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_TEXT = TypeAdapter(str)
_INT = TypeAdapter(int)
type IndexedEntityKind = Literal["source", "page", "memory"]


@dataclass(frozen=True, slots=True)
class IndexRunResult:
    item_id: str | None
    state: Literal["idle", "indexed", "waiting", "failed"]
    chunk_count: int = 0
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class IndexTarget:
    item_id: str
    workspace_id: str
    entity_kind: IndexedEntityKind
    entity_id: str
    revision_id: str
    admission_revision: int | None
    lease_generation: int


@dataclass(frozen=True, slots=True)
class IndexChunk:
    chunk_id: str
    content: str
    index_content: str


@dataclass(frozen=True, slots=True)
class _IndexCommit:
    state: Literal["indexed", "waiting", "lease_lost"]
    chunk_count: int = 0


@dataclass(frozen=True, slots=True)
class KnowledgeIndexWorker:
    _repository: SqliteKnowledgeRepository

    def run_once(
        self,
        workspace_id: str,
        worker_id: str,
        now: datetime,
        lease_for: timedelta = timedelta(minutes=1),
    ) -> IndexRunResult:
        item = self._claim(workspace_id, worker_id, now, lease_for)
        if item is None:
            return IndexRunResult(item_id=None, state="idle")
        try:
            drafts = self.authoritative_chunks(item)
        except sqlite3.Error:
            self._finish(item, worker_id, "failed", "index_storage_error")
            return IndexRunResult(item.item_id, "failed", error_code="index_storage_error")
        except (FrozenInstanceError, KnowledgeFileStoreError, OSError, ValueError):
            self._finish(item, worker_id, "failed", "index_content_unavailable")
            return IndexRunResult(item.item_id, "failed", error_code="index_content_unavailable")
        if drafts is None:
            self._finish(item, worker_id, "completed", None)
            return IndexRunResult(item.item_id, "waiting")
        try:
            committed = self._replace_chunks_and_finish(item, drafts, worker_id)
        except sqlite3.Error:
            self._finish(item, worker_id, "failed", "index_storage_error")
            return IndexRunResult(item.item_id, "failed", error_code="index_storage_error")
        return _result_from_commit(item.item_id, committed)

    def _claim(
        self,
        workspace_id: str,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> IndexTarget | None:
        with self._repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                SELECT item_id,entity_kind,entity_id,revision_id,
                    admission_revision,lease_generation
                FROM index_outbox
                WHERE workspace_id=? AND (
                    state='pending' OR (state='running' AND lease_expires_at<?))
                ORDER BY item_id LIMIT 1
                """,
                    (workspace_id, now.isoformat()),
                ).fetchone(),
            )
            if row is None:
                return None
            item = IndexTarget(
                item_id=_TEXT.validate_python(row[0]),
                workspace_id=workspace_id,
                entity_kind=cast("IndexedEntityKind", _TEXT.validate_python(row[1])),
                entity_id=_TEXT.validate_python(row[2]),
                revision_id=_TEXT.validate_python(row[3]),
                admission_revision=None if row[4] is None else _INT.validate_python(row[4]),
                lease_generation=_INT.validate_python(row[5]) + 1,
            )
            cursor = connection.execute(
                """
                UPDATE index_outbox SET state='running',lease_owner=?,lease_expires_at=?,
                    lease_generation=?,error_code=NULL
                WHERE item_id=? AND (state='pending' OR (state='running' AND lease_expires_at<?))
                """,
                (
                    worker_id,
                    (now + lease_for).isoformat(),
                    item.lease_generation,
                    item.item_id,
                    now.isoformat(),
                ),
            )
            return item if cursor.rowcount == 1 else None

    def authoritative_chunks(self, item: IndexTarget) -> tuple[IndexChunk, ...] | None:
        match item.entity_kind:
            case "source":
                return self._source_drafts(item)
            case "page":
                return self._page_drafts(item)
            case "memory":
                return self._memory_drafts(item)

    def _source_drafts(self, item: IndexTarget) -> tuple[IndexChunk, ...] | None:
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                SELECT source.source_json FROM sources AS source
                JOIN source_heads AS head USING(workspace_id,source_id)
                WHERE source.workspace_id=? AND source.source_id=? AND head.revision_id=?
                AND source.visibility='searchable' AND source.admission_revision=?
                """,
                    (item.workspace_id, item.entity_id, item.revision_id, item.admission_revision),
                ).fetchone(),
            )
            if row is None:
                return None
            source = Source.model_validate_json(_TEXT.validate_python(row[0]))
            segment_rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT segment_json FROM segments WHERE workspace_id=? AND source_id=?
                AND revision_id=? ORDER BY extraction_version,segment_id""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchall(),
            )
            digest_row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT sha256 FROM source_files
                WHERE workspace_id=? AND source_id=? AND revision_id=?
                AND file_kind='extracted.md'""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchone(),
            )
        if digest_row is None:
            return None
        target = SourceRevisionTarget(
            source.source_id, source.revision_id, SourceFileKind.EXTRACTED
        )
        body = self._repository.files.read(
            self._repository.files.published(target, _TEXT.validate_python(digest_row[0]))
        )
        text = body.decode("utf-8", errors="replace")
        segments = tuple(
            SourceSegment.model_validate_json(_TEXT.validate_python(row[0])) for row in segment_rows
        )
        if not segments:
            return (self._draft(f"source:{source.source_id}:{source.revision_id}:body", text),)
        return tuple(
            self._draft(
                f"source:{source.source_id}:{source.revision_id}:{segment.segment_id}",
                text[segment.quote_range.start : segment.quote_range.end],
            )
            for segment in segments
        )

    def _page_drafts(self, item: IndexTarget) -> tuple[IndexChunk, ...] | None:
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                SELECT page.page_json,revision.revision_json,revision.body_sha256
                FROM wiki_pages AS page
                JOIN knowledge_heads AS head USING(workspace_id,page_id)
                JOIN knowledge_revisions AS revision ON revision.workspace_id=head.workspace_id
                    AND revision.page_id=head.page_id AND revision.revision_id=head.revision_id
                WHERE page.workspace_id=? AND page.page_id=? AND head.revision_id=?
                AND page.status='active'
                """,
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchone(),
            )
        if row is None:
            return None
        page = WikiPage.model_validate_json(_TEXT.validate_python(row[0]))
        revision = KnowledgeRevision.model_validate_json(_TEXT.validate_python(row[1]))
        target = KnowledgeRevisionTarget(page.page_id, item.revision_id)
        body = self._repository.files.read(
            self._repository.files.published(target, _TEXT.validate_python(row[2]))
        )
        title = " ".join(
            (
                page.title,
                *page.aliases,
                *(f"{attribute.key} {attribute.value}" for attribute in page.attributes),
            )
        )
        body_chunk = (
            self._draft(
                f"page:{page.page_id}:{item.revision_id}:body",
                f"{title}\n{body.decode('utf-8', errors='replace')}",
            ),
        )
        claim_chunks = tuple(
            self._draft(
                f"page:{page.page_id}:{item.revision_id}:claim:{claim.claim_id}",
                f"{title}\n{claim.statement}",
            )
            for claim in revision.claims
            if claim.status.value in {"active", "contested"}
        )
        return (*body_chunk, *claim_chunks)

    def _memory_drafts(self, item: IndexTarget) -> tuple[IndexChunk, ...] | None:
        with self._repository.connection() as connection:
            document = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT document_json FROM memory_documents AS document
                JOIN memory_heads AS head USING(workspace_id,document_id)
                WHERE document.workspace_id=? AND document.document_id=?
                AND head.revision_id=?""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchone(),
            )
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT entry_json FROM memory_entries
                WHERE workspace_id=? AND document_id=?
                AND memory_revision_id=? AND status IN ('active','contested')
                AND dependency_state='current' ORDER BY entry_id""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchall(),
            )
        if document is None:
            return None
        entries = tuple(
            MemoryEntry.model_validate_json(_TEXT.validate_python(row[0])) for row in rows
        )
        return tuple(
            self._draft(f"memory:{item.entity_id}:{item.revision_id}:{entry.entry_id}", entry.text)
            for entry in entries
        )

    @staticmethod
    def _draft(chunk_id: str, content: str) -> IndexChunk:
        return IndexChunk(chunk_id, content, _index_content(content))

    def _replace_chunks_and_finish(
        self,
        item: IndexTarget,
        drafts: tuple[IndexChunk, ...],
        worker_id: str,
    ) -> _IndexCommit:
        with self._repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            lease = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT 1 FROM index_outbox WHERE item_id=? AND state='running'
                AND lease_owner=? AND lease_generation=?""",
                    (item.item_id, worker_id, item.lease_generation),
                ).fetchone(),
            )
            if lease is None:
                return _IndexCommit("lease_lost")
            if not _target_is_current(connection, item):
                _ = connection.execute(
                    """UPDATE index_outbox SET state='completed',lease_owner=NULL,
                    lease_expires_at=NULL,error_code=NULL WHERE item_id=? AND state='running'
                    AND lease_owner=? AND lease_generation=?""",
                    (item.item_id, worker_id, item.lease_generation),
                )
                return _IndexCommit("waiting")
            _ = connection.execute(
                """DELETE FROM chunks_fts WHERE chunk_id IN (
                SELECT chunk_id FROM chunks
                WHERE workspace_id=? AND entity_kind=? AND entity_id=? AND revision_id=?)""",
                (item.workspace_id, item.entity_kind, item.entity_id, item.revision_id),
            )
            _ = connection.execute(
                """DELETE FROM chunks WHERE workspace_id=? AND entity_kind=?
                AND entity_id=? AND revision_id=?""",
                (item.workspace_id, item.entity_kind, item.entity_id, item.revision_id),
            )
            for draft in drafts:
                _ = connection.execute(
                    """INSERT INTO chunks(
                    chunk_id,workspace_id,entity_kind,entity_id,revision_id,content,
                    content_sha256,chunker_version) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        draft.chunk_id,
                        item.workspace_id,
                        item.entity_kind,
                        item.entity_id,
                        item.revision_id,
                        draft.content,
                        sha256(draft.content.encode()).hexdigest(),
                        "chunker.v1",
                    ),
                )
                _ = connection.execute(
                    "INSERT INTO chunks_fts(chunk_id,content) VALUES (?,?)",
                    (draft.chunk_id, draft.index_content),
                )
            _ = connection.execute(
                """UPDATE index_outbox SET state='completed',lease_owner=NULL,
                lease_expires_at=NULL,error_code=NULL WHERE item_id=? AND state='running'
                AND lease_owner=? AND lease_generation=?""",
                (item.item_id, worker_id, item.lease_generation),
            )
            return _IndexCommit("indexed", len(drafts))

    def _finish(
        self,
        item: IndexTarget,
        worker_id: str,
        state: Literal["completed", "failed"],
        error_code: str | None,
    ) -> None:
        with self._repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _ = connection.execute(
                """UPDATE index_outbox
                SET state=?,lease_owner=NULL,lease_expires_at=NULL,error_code=?
                WHERE item_id=? AND state='running' AND lease_owner=? AND lease_generation=?""",
                (state, error_code, item.item_id, worker_id, item.lease_generation),
            )


def normalized_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _target_is_current(connection: sqlite3.Connection, item: IndexTarget) -> bool:
    match item.entity_kind:
        case "source":
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT 1 FROM source_heads AS head JOIN sources AS source
                USING(workspace_id,source_id) WHERE head.workspace_id=? AND head.source_id=?
                AND head.revision_id=? AND source.visibility='searchable'
                AND source.admission_revision=? AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=head.workspace_id
                AND tomb.target_id IN (head.source_id,head.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                    (
                        item.workspace_id,
                        item.entity_id,
                        item.revision_id,
                        item.admission_revision,
                    ),
                ).fetchone(),
            )
        case "page":
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                """SELECT 1 FROM knowledge_heads AS head JOIN wiki_pages AS page
                USING(workspace_id,page_id) WHERE head.workspace_id=? AND head.page_id=?
                AND head.revision_id=? AND page.status='active' AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=head.workspace_id
                AND tomb.target_id IN (head.page_id,head.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchone(),
            )
        case "memory":
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT 1 FROM memory_heads WHERE workspace_id=? AND document_id=?
                AND revision_id=? AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=memory_heads.workspace_id
                AND tomb.target_id IN (memory_heads.document_id,memory_heads.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                    (item.workspace_id, item.entity_id, item.revision_id),
                ).fetchone(),
            )
    return row is not None


def _result_from_commit(item_id: str, committed: _IndexCommit) -> IndexRunResult:
    match committed.state:
        case "indexed":
            return IndexRunResult(item_id, "indexed", committed.chunk_count)
        case "waiting":
            return IndexRunResult(item_id, "waiting")
        case "lease_lost":
            return IndexRunResult(item_id, "failed", error_code="index_lease_lost")


def korean_compact_bigrams(text: str) -> str:
    compact = "".join(char for char in normalized_text(text) if "가" <= char <= "힣")
    return " ".join(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))


def _index_content(content: str) -> str:
    normalized = normalized_text(content)
    return f"{normalized} {korean_compact_bigrams(normalized)}"


__all__ = [
    "IndexChunk",
    "IndexRunResult",
    "IndexTarget",
    "KnowledgeIndexWorker",
    "korean_compact_bigrams",
    "normalized_text",
]
