from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError, dataclass
from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import TypeAdapter

from ads_booster.contracts.knowledge_selection import RetrievalStatus
from ads_booster.knowledge.contracts import (
    ActorContext,
    Claim,
    ClaimStatus,
    ConversationEvent,
    EvidenceKind,
    EvidenceRef,
    KnowledgeRevision,
    MemoryEntry,
    Source,
    SourceSegment,
    WikiPage,
)
from ads_booster.knowledge.errors import AccessDeniedError, EvidenceResolutionError
from ads_booster.knowledge.file_paths import KnowledgeFileStoreError
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.indexing import (
    IndexTarget,
    KnowledgeIndexWorker,
    korean_compact_bigrams,
    normalized_text,
)
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.scope_contracts import AccessScope

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from ads_booster.knowledge.repository_protocol import KnowledgeRepository
    from ads_booster.knowledge.wiki_contracts import PageAttribute

_TEXT = TypeAdapter(str)
_INT = TypeAdapter(int)
_MAX_HITS = 20
_MAX_LEXICAL_CANDIDATES = 400
_MAX_STALE_ENTITIES = 100
_SNIPPET_LIMIT = 1_200
type ConflictRole = Literal["claim", "counter_claim", "counter_evidence"]


@unique
class SearchCorpus(StrEnum):
    REFERENCE = "reference"
    WIKI = "wiki"
    MEMORY = "memory"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    corpus: SearchCorpus = SearchCorpus.ALL
    attributes: Mapping[str, str] | None = None
    brand_id: str | None = None
    limit: int = 8
    historical: bool = False


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: SearchCorpus
    entity_id: str
    revision_id: str
    citation_id: str
    title: str
    snippet: str
    score: float
    related_page_ids: tuple[str, ...] = ()
    needs_review: bool = False
    claim_id: str | None = None
    canonical_root_ids: tuple[str, ...] = ()
    conflict_group_id: str | None = None
    conflict_role: ConflictRole | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    status: RetrievalStatus
    index_pending: bool
    total_count: int


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    chunk_id: str
    score: float


class VectorSearchPort(Protocol):
    def search(self, query: str, limit: int) -> tuple[VectorCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class _Candidate:
    chunk_id: str
    kind: SearchCorpus
    entity_id: str
    revision_id: str
    content: str
    lexical_rank: int


@dataclass(frozen=True, slots=True)
class _CounterContext:
    actor: ActorContext
    score: float
    group_id: str
    now: datetime
    historical: bool


@dataclass(frozen=True, slots=True)
class KnowledgeRetriever:
    _repository: KnowledgeRepository
    _vector_port: VectorSearchPort | None = None

    def search(self, actor: ActorContext, request: SearchRequest, *, now: datetime) -> SearchResult:
        if not request.query.strip():
            return SearchResult((), RetrievalStatus.READY, self._has_pending(actor.workspace_id), 0)
        limit = min(max(request.limit, 1), _MAX_HITS)
        lexical = self._lexical(actor.workspace_id, request)
        stale, stale_failed = self._stale_candidates(actor, request, now)
        vector_matches, vector_failed = self._vector_candidates(request, limit)
        vector_chunks = self._vector_chunk_candidates(actor.workspace_id, request, vector_matches)
        candidates = _unique_candidates((*lexical, *stale, *vector_chunks))
        visible_items: list[tuple[_Candidate, SearchHit]] = []
        content_failed = False
        for candidate in candidates:
            try:
                hit = self._visible_hit(actor, request, candidate, now, {})
            except (FrozenInstanceError, KnowledgeFileStoreError, OSError, ValueError):
                content_failed = True
                continue
            if hit is not None:
                visible_items.append((candidate, hit))
        visible = tuple(visible_items)
        authorized_ids = frozenset(candidate.chunk_id for candidate, _ in visible)
        vector_ranks = _vector_ranks(vector_matches, authorized_ids)
        ranked = tuple(
            sorted(
                _authorized_scores(visible, vector_ranks),
                key=lambda item: (-item.score, item.entity_id, item.citation_id),
            )
        )
        groups = self._result_groups(actor, request, ranked, now)
        selected = _select_groups(groups, limit)
        pending = self._has_pending(actor.workspace_id)
        status = (
            RetrievalStatus.DEGRADED
            if vector_failed or stale_failed or content_failed
            else (RetrievalStatus.INDEX_PENDING if pending else RetrievalStatus.READY)
        )
        return SearchResult(selected, status, pending, sum(len(group) for group in groups))

    def _lexical(self, workspace_id: str, request: SearchRequest) -> tuple[_Candidate, ...]:
        expression = _fts_expression(request.query)
        if expression is None:
            return ()
        with self._repository.connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT chunk.chunk_id,chunk.entity_kind,chunk.entity_id,chunk.revision_id,
                    chunk.content FROM chunks_fts JOIN chunks AS chunk USING(chunk_id)
                    WHERE chunk.workspace_id=? AND chunks_fts MATCH ?
                    ORDER BY bm25(chunks_fts),chunk.chunk_id LIMIT ?""",
                    (workspace_id, expression, _MAX_LEXICAL_CANDIDATES),
                ).fetchall(),
            )
        allowed = _corpus_kinds(request.corpus)
        matched = tuple(row for row in rows if _TEXT.validate_python(row[1]) in allowed)
        return tuple(
            _Candidate(
                chunk_id=_TEXT.validate_python(row[0]),
                kind=_entity_corpus(_TEXT.validate_python(row[1])),
                entity_id=_TEXT.validate_python(row[2]),
                revision_id=_TEXT.validate_python(row[3]),
                content=_TEXT.validate_python(row[4]),
                lexical_rank=rank,
            )
            for rank, row in enumerate(matched, start=1)
        )

    def _vector_candidates(
        self,
        request: SearchRequest,
        limit: int,
    ) -> tuple[tuple[VectorCandidate, ...], bool]:
        if self._vector_port is None:
            return (), False
        try:
            candidates = self._vector_port.search(request.query, limit)
        except RuntimeError:
            return (), True
        return (
            tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.chunk_id))),
            False,
        )

    def _vector_chunk_candidates(
        self,
        workspace_id: str,
        request: SearchRequest,
        vector_matches: tuple[VectorCandidate, ...],
    ) -> tuple[_Candidate, ...]:
        if not vector_matches:
            return ()
        ids = tuple(candidate.chunk_id for candidate in vector_matches)
        with self._repository.connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT chunk_id,entity_kind,entity_id,revision_id,content FROM chunks
                    WHERE workspace_id=? AND chunk_id IN (SELECT value FROM json_each(?))""",
                    (workspace_id, json.dumps(ids)),
                ).fetchall(),
            )
        by_id = {_TEXT.validate_python(row[0]): row for row in rows}
        allowed = _corpus_kinds(request.corpus)
        return tuple(
            _Candidate(
                chunk_id=candidate.chunk_id,
                kind=_entity_corpus(_TEXT.validate_python(row[1])),
                entity_id=_TEXT.validate_python(row[2]),
                revision_id=_TEXT.validate_python(row[3]),
                content=_TEXT.validate_python(row[4]),
                    lexical_rank=-1,
            )
            for candidate in vector_matches
            if (row := by_id.get(candidate.chunk_id)) is not None
            and _TEXT.validate_python(row[1]) in allowed
        )

    def _visible_hit(
        self,
        actor: ActorContext,
        request: SearchRequest,
        candidate: _Candidate,
        now: datetime,
        vector_ranks: Mapping[str, int],
    ) -> SearchHit | None:
        match candidate.kind:
            case SearchCorpus.REFERENCE:
                return self._source_hit(actor, request, candidate, now, vector_ranks)
            case SearchCorpus.WIKI:
                return self._page_hit(actor, request, candidate, now, vector_ranks)
            case SearchCorpus.MEMORY:
                return self._memory_hit(actor, request, candidate, now, vector_ranks)
            case SearchCorpus.ALL:
                return None

    def _source_hit(
        self,
        actor: ActorContext,
        request: SearchRequest,
        candidate: _Candidate,
        now: datetime,
        vector_ranks: Mapping[str, int],
    ) -> SearchHit | None:
        with self._repository.connection() as connection:
            if request.historical:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT revision.revision_json FROM source_revisions AS revision
                        JOIN sources AS source USING(workspace_id,source_id)
                        WHERE source.workspace_id=? AND source.source_id=?
                        AND revision.revision_id=? AND source.visibility='searchable'
                        AND NOT EXISTS (SELECT 1 FROM tombstones AS tomb
                        WHERE tomb.workspace_id=source.workspace_id
                        AND tomb.target_id IN (source.source_id,revision.revision_id)
                        AND tomb.state IN ('blocked','purge_pending','purged'))""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id),
                    ).fetchone(),
                )
            else:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT source_json FROM sources AS source
                JOIN source_heads AS head USING(workspace_id,source_id)
                WHERE source.workspace_id=? AND source.source_id=? AND head.revision_id=?
                AND source.visibility='searchable' AND NOT EXISTS (SELECT 1 FROM tombstones AS tomb
                WHERE tomb.workspace_id=source.workspace_id
                AND tomb.target_id IN (source.source_id,head.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id),
                    ).fetchone(),
                )
        if row is None:
            return None
        source = Source.model_validate_json(_TEXT.validate_python(row[0]))
        if not _readable(actor, source.scope, now):
            return None
        if not request.historical:
            stored = self._repository.read_source(actor, source.source_id)
            if stored is None or stored.source.revision_id != candidate.revision_id:
                return None
        title = source.sanitized_locator
        if len(normalized_text(request.query)) == 1 and normalized_text(
            request.query
        ) not in normalized_text(title):
            return None
        return SearchHit(
            SearchCorpus.REFERENCE,
            source.source_id,
            source.revision_id,
            candidate.chunk_id,
            title,
            _snippet(candidate.content),
            _rrf_score(candidate, vector_ranks),
            canonical_root_ids=self._source_roots(actor, source, candidate, now),
        )

    def _page_hit(
        self,
        actor: ActorContext,
        request: SearchRequest,
        candidate: _Candidate,
        now: datetime,
        vector_ranks: Mapping[str, int],
    ) -> SearchHit | None:
        with self._repository.connection() as connection:
            if request.historical:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT revision.revision_json,revision.revision_json,
                        revision.body_sha256 FROM knowledge_revisions AS revision
                        JOIN wiki_pages AS page USING(workspace_id,page_id)
                        WHERE page.workspace_id=? AND page.page_id=?
                        AND revision.revision_id=? AND page.status!='retracted'
                        AND NOT EXISTS (SELECT 1 FROM tombstones AS tomb
                        WHERE tomb.workspace_id=page.workspace_id
                        AND tomb.target_id IN (page.page_id,revision.revision_id)
                        AND tomb.state IN ('blocked','purge_pending','purged'))""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id),
                    ).fetchone(),
                )
            else:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT page.page_json,revision.revision_json,
                        revision.body_sha256 FROM wiki_pages AS page
                JOIN knowledge_heads AS head USING(workspace_id,page_id)
                JOIN knowledge_revisions AS revision ON revision.workspace_id=head.workspace_id
                AND revision.page_id=head.page_id AND revision.revision_id=head.revision_id
                WHERE page.workspace_id=? AND page.page_id=? AND head.revision_id=?
                AND page.status='active'
                AND NOT EXISTS (
                SELECT 1 FROM claim_visibility_fences AS fence
                WHERE fence.workspace_id=revision.workspace_id
                AND fence.dependent_revision_id=revision.revision_id)
                AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=page.workspace_id
                AND tomb.target_id IN (page.page_id,head.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id),
                    ).fetchone(),
                )
        if row is None:
            return None if request.historical else self._redirect_hit(actor, candidate, now)
        scope, title, aliases, attributes = _page_metadata(
            _TEXT.validate_python(row[0]), request.historical
        )
        if not _readable(actor, scope, now) or not _attributes_match(
            attributes, request.attributes
        ):
            return None
        if len(normalized_text(request.query)) == 1 and normalized_text(
            request.query
        ) not in _title_terms(title, aliases):
            return None
        revision = KnowledgeRevision.model_validate_json(_TEXT.validate_python(row[1]))
        claim_id = _claim_id(candidate.chunk_id)
        selected_claim = next(
            (claim for claim in revision.claims if claim.claim_id == claim_id),
            None,
        )
        roots = (
            (f"page:{candidate.entity_id}:{candidate.revision_id}:{_TEXT.validate_python(row[2])}",)
            if selected_claim is None
            else self._claim_roots(
                actor,
                selected_claim,
                candidate.revision_id,
                revision.scope,
                now,
            )
        )
        if not roots:
            return None
        stored_page = self._repository.read_page(
            actor,
            candidate.entity_id,
            candidate.revision_id if request.historical else None,
        )
        if stored_page is None or stored_page.revision.revision_id != candidate.revision_id:
            return None
        return SearchHit(
            SearchCorpus.WIKI,
            candidate.entity_id,
            candidate.revision_id,
            candidate.chunk_id,
            title,
            _snippet(candidate.content),
            _rrf_score(candidate, vector_ranks),
            self._related(actor, candidate.entity_id, candidate.revision_id, now),
            claim_id=None if selected_claim is None else selected_claim.claim_id,
            canonical_root_ids=roots,
        )

    def _memory_hit(
        self,
        actor: ActorContext,
        request: SearchRequest,
        candidate: _Candidate,
        now: datetime,
        vector_ranks: Mapping[str, int],
    ) -> SearchHit | None:
        entry_id = candidate.chunk_id.rsplit(":", 1)[-1]
        with self._repository.connection() as connection:
            if request.historical:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT document.kind,document.local_date,document.brand_id,
                        entry.entry_json
                        FROM memory_documents AS document JOIN memory_entries AS entry
                        USING(workspace_id,document_id) WHERE document.workspace_id=?
                        AND document.document_id=? AND entry.memory_revision_id=?
                        AND entry.entry_id=? AND entry.status IN (
                        'active','contested','superseded','retracted')
                        AND entry.dependency_state='current'""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id, entry_id),
                    ).fetchone(),
                )
            else:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """SELECT document.kind,document.local_date,document.brand_id,
                        entry.entry_json
                FROM memory_documents AS document
                JOIN memory_heads AS head USING(workspace_id,document_id)
                JOIN memory_entries AS entry ON entry.workspace_id=head.workspace_id
                AND entry.document_id=head.document_id AND entry.memory_revision_id=head.revision_id
                WHERE document.workspace_id=? AND document.document_id=?
                AND head.revision_id=? AND entry.entry_id=?
                AND entry.status IN ('active','contested') AND entry.dependency_state='current'
                AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=document.workspace_id
                AND tomb.target_id IN (document.document_id,entry.entry_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                        (actor.workspace_id, candidate.entity_id, candidate.revision_id, entry_id),
                    ).fetchone(),
                )
        if row is None:
            return None
        kind = _TEXT.validate_python(row[0])
        brand_id = None if row[2] is None else _TEXT.validate_python(row[2])
        if not brand_target_matches(kind, brand_id, request.brand_id):
            return None
        entry = MemoryEntry.model_validate_json(_TEXT.validate_python(row[3]))
        if not _readable(actor, entry.scope, now):
            return None
        stored_memory = self._repository.read_memory(
            actor,
            candidate.entity_id,
            candidate.revision_id if request.historical else None,
        )
        if stored_memory is None or stored_memory.revision.revision_id != candidate.revision_id:
            return None
        local_date = _TEXT.validate_python(row[1]) if row[1] is not None else candidate.entity_id
        title = f"{kind}:{local_date}"
        return SearchHit(
            SearchCorpus.MEMORY,
            candidate.entity_id,
            candidate.revision_id,
            entry.entry_id,
            title,
            _snippet(candidate.content),
            _rrf_score(candidate, vector_ranks),
            needs_review=entry.review_after is not None and entry.review_after <= now,
            canonical_root_ids=self._entry_roots(actor, entry, candidate.revision_id, now),
        )

    def _redirect_hit(
        self,
        actor: ActorContext,
        candidate: _Candidate,
        now: datetime,
    ) -> SearchHit | None:
        target_id = self._repository.resolve_page_id(actor, candidate.entity_id)
        if target_id == candidate.entity_id:
            return None
        stored = self._repository.read_page(actor, target_id)
        if stored is None:
            return None
        return SearchHit(
            SearchCorpus.WIKI,
            stored.page.page_id,
            stored.revision.revision_id,
            f"page:{stored.page.page_id}:{stored.revision.revision_id}:body",
            stored.page.title,
            _snippet(stored.body.decode("utf-8", errors="replace")),
            0,
            self._related(actor, stored.page.page_id, stored.revision.revision_id, now),
            canonical_root_ids=(
                (
                    f"page:{stored.page.page_id}:{stored.revision.revision_id}:"
                    f"{stored.revision.body_sha256}"
                ),
            ),
        )

    def _related(
        self,
        actor: ActorContext,
        page_id: str,
        revision_id: str,
        now: datetime,
    ) -> tuple[str, ...]:
        with self._repository.connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT relation.to_page_id,page.page_json FROM page_relations AS relation
                JOIN wiki_pages AS page ON page.workspace_id=relation.workspace_id
                AND page.page_id=relation.to_page_id
                JOIN knowledge_heads AS head ON head.workspace_id=page.workspace_id
                AND head.page_id=page.page_id
                WHERE relation.workspace_id=? AND relation.from_page_id=?
                AND relation.from_revision_id=? AND relation.kind='related_to'
                AND page.status='active' AND NOT EXISTS (
                SELECT 1 FROM tombstones AS tomb WHERE tomb.workspace_id=page.workspace_id
                AND tomb.target_id IN (page.page_id,head.revision_id)
                AND tomb.state IN ('blocked','purge_pending','purged'))
                ORDER BY relation.to_page_id""",
                    (actor.workspace_id, page_id, revision_id),
                ).fetchall(),
            )
        readable = tuple(
            _TEXT.validate_python(row[0])
            for row in rows
            if _readable(
                actor,
                WikiPage.model_validate_json(_TEXT.validate_python(row[1])).scope,
                now,
            )
        )
        return readable[:1]

    def _stale_candidates(
        self,
        actor: ActorContext,
        request: SearchRequest,
        now: datetime,
    ) -> tuple[tuple[_Candidate, ...], bool]:
        allowed = _corpus_kinds(request.corpus)
        readable_scope_keys = tuple(
            scope_key(grant.scope)
            for grant in actor.grants
            if grant.capability.value == "read"
            and grant.policy_epoch == actor.policy_epoch
            and grant.effective_at <= now
            and (grant.expires_at is None or now < grant.expires_at)
        )
        if not readable_scope_keys:
            return (), False
        scope_keys_json = json.dumps(readable_scope_keys)
        with self._repository.connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT item_id,entity_kind,entity_id,revision_id,
                        admission_revision,lease_generation
                    FROM index_outbox AS item
                    WHERE workspace_id=? AND state IN ('pending','running','failed')
                    AND ((entity_kind='source' AND ?)
                        OR (entity_kind='page' AND ?)
                        OR (entity_kind='memory' AND ?))
                    AND ((entity_kind='source' AND EXISTS (
                        SELECT 1 FROM source_heads AS head JOIN sources AS source
                        USING(workspace_id,source_id) WHERE head.workspace_id=item.workspace_id
                        AND head.source_id=item.entity_id AND head.revision_id=item.revision_id
                        AND source.visibility='searchable' AND source.scope_key IN (
                            SELECT value FROM json_each(?)) AND NOT EXISTS (
                            SELECT 1 FROM tombstones AS tomb
                            WHERE tomb.workspace_id=head.workspace_id
                            AND tomb.target_id IN (head.source_id,head.revision_id)
                            AND tomb.state IN ('blocked','purge_pending','purged'))))
                    OR (entity_kind='page' AND EXISTS (
                        SELECT 1 FROM knowledge_heads AS head JOIN wiki_pages AS page
                        USING(workspace_id,page_id) WHERE head.workspace_id=item.workspace_id
                        AND head.page_id=item.entity_id AND head.revision_id=item.revision_id
                        AND page.status='active' AND page.scope_key IN (
                            SELECT value FROM json_each(?)) AND NOT EXISTS (
                            SELECT 1 FROM tombstones AS tomb
                            WHERE tomb.workspace_id=head.workspace_id
                            AND tomb.target_id IN (head.page_id,head.revision_id)
                            AND tomb.state IN ('blocked','purge_pending','purged'))))
                    OR (entity_kind='memory' AND EXISTS (
                        SELECT 1 FROM memory_heads AS head WHERE head.workspace_id=item.workspace_id
                        AND head.document_id=item.entity_id AND head.revision_id=item.revision_id
                        AND EXISTS (SELECT 1 FROM memory_documents AS document
                        WHERE document.workspace_id=head.workspace_id
                        AND document.document_id=head.document_id AND document.scope_key IN (
                            SELECT value FROM json_each(?))) AND NOT EXISTS (
                            SELECT 1 FROM tombstones AS tomb
                            WHERE tomb.workspace_id=head.workspace_id
                            AND tomb.target_id IN (head.document_id,head.revision_id)
                            AND tomb.state IN ('blocked','purge_pending','purged')))))
                    ORDER BY item_id LIMIT ?
                    """,
                    (
                        actor.workspace_id,
                        "source" in allowed,
                        "page" in allowed,
                        "memory" in allowed,
                        scope_keys_json,
                        scope_keys_json,
                        scope_keys_json,
                        _MAX_STALE_ENTITIES + 1,
                    ),
                ).fetchall(),
            )
        worker = KnowledgeIndexWorker(self._repository)
        candidates: list[_Candidate] = []
        failed = False
        for row in rows[:_MAX_STALE_ENTITIES]:
            target = IndexTarget(
                item_id=_TEXT.validate_python(row[0]),
                workspace_id=actor.workspace_id,
                entity_kind=cast(
                    "Literal['source', 'page', 'memory']", _TEXT.validate_python(row[1])
                ),
                entity_id=_TEXT.validate_python(row[2]),
                revision_id=_TEXT.validate_python(row[3]),
                admission_revision=None if row[4] is None else _INT.validate_python(row[4]),
                lease_generation=_INT.validate_python(row[5]),
            )
            try:
                chunks = worker.authoritative_chunks(target)
            except (
                FrozenInstanceError,
                KnowledgeFileStoreError,
                OSError,
                ValueError,
                sqlite3.Error,
            ):
                failed = True
                continue
            if chunks is None:
                continue
            candidates.extend(
                        _Candidate(
                            chunk_id=chunk.chunk_id,
                            kind=_entity_corpus(target.entity_kind),
                            entity_id=target.entity_id,
                            revision_id=target.revision_id,
                            content=chunk.content,
                            lexical_rank=0,
                        )
                for chunk in chunks
                if _query_matches(chunk.index_content, request.query)
            )
        return tuple(candidates), failed

    def _source_roots(
        self,
        actor: ActorContext,
        source: Source,
        candidate: _Candidate,
        now: datetime,
    ) -> tuple[str, ...]:
        segment_id = candidate.chunk_id.rsplit(":", 1)[-1]
        if segment_id == "body":
            return (f"source:{source.source_id}:{source.revision_id}:{source.sha256}",)
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT segment_json FROM segments WHERE workspace_id=? AND source_id=?
                AND revision_id=? AND segment_id=? ORDER BY extraction_version DESC LIMIT 1""",
                    (actor.workspace_id, source.source_id, source.revision_id, segment_id),
                ).fetchone(),
            )
        if row is None:
            return ()
        segment = SourceSegment.model_validate_json(_TEXT.validate_python(row[0]))
        reference = EvidenceRef(
            evidence_kind=EvidenceKind.SOURCE_SEGMENT,
            evidence_id=segment.segment_id,
            revision_id=segment.revision_id,
            segment_id=segment.segment_id,
            quote_sha256=segment.content_sha256,
            scope=source.scope,
        )
        return self._evidence_roots(actor, reference, now, frozenset())

    def _claim_roots(
        self,
        actor: ActorContext,
        claim: Claim,
        revision_id: str,
        scope: AccessScope,
        now: datetime,
    ) -> tuple[str, ...]:
        reference = EvidenceRef(
            evidence_kind=EvidenceKind.CLAIM,
            evidence_id=claim.claim_id,
            revision_id=revision_id,
            scope=scope,
        )
        return self._evidence_roots(actor, reference, now, frozenset())

    def _entry_roots(
        self,
        actor: ActorContext,
        entry: MemoryEntry,
        revision_id: str,
        now: datetime,
    ) -> tuple[str, ...]:
        reference = EvidenceRef(
            evidence_kind=EvidenceKind.MEMORY_ENTRY,
            evidence_id=entry.entry_id,
            revision_id=revision_id,
            scope=entry.scope,
        )
        return self._evidence_roots(actor, reference, now, frozenset())

    def _evidence_roots(
        self,
        actor: ActorContext,
        reference: EvidenceRef,
        now: datetime,
        seen: frozenset[tuple[str, str, str]],
    ) -> tuple[str, ...]:
        key = (reference.evidence_kind.value, reference.evidence_id, reference.revision_id)
        if key in seen or not _readable(actor, reference.scope, now):
            return ()
        try:
            resolved = self._repository.resolve_evidence(actor, reference)
        except (AccessDeniedError, EvidenceResolutionError):
            return ()
        next_seen = seen | {key}
        match resolved:
            case SourceSegment():
                return (
                    f"source:{resolved.segment_id}:{resolved.revision_id}:{resolved.content_sha256}",
                )
            case Claim():
                upstreams = self._edge_upstreams(reference) or resolved.evidence_refs
                roots = {
                    root
                    for upstream in upstreams
                    for root in self._evidence_roots(actor, upstream, now, next_seen)
                }
                return tuple(sorted(roots))
            case MemoryEntry():
                upstreams = self._edge_upstreams(reference) or resolved.source_refs
                roots = {
                    root
                    for upstream in upstreams
                    for root in self._evidence_roots(actor, upstream, now, next_seen)
                }
                return tuple(sorted(roots))
            case ConversationEvent():
                digest = sha256(resolved.text.encode()).hexdigest()
                return (f"conversation:{resolved.message_id}:{resolved.revision}:{digest}",)

    def _edge_upstreams(self, reference: EvidenceRef) -> tuple[EvidenceRef, ...]:
        with self._repository.connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """SELECT target.entity_kind,target.entity_id,target.revision_id,
                    target.segment_id,scope.scope_json FROM evidence_nodes AS owner
                    JOIN evidence_edges AS edge ON edge.from_node_id=owner.node_id
                    JOIN evidence_nodes AS target ON target.node_id=edge.to_node_id
                    JOIN access_scopes AS scope ON scope.scope_key=target.scope_key
                    WHERE owner.workspace_id=? AND owner.entity_kind=? AND owner.entity_id=?
                    AND owner.revision_id=? AND edge.edge_kind IN (
                    'supports','derived_from','quotes') ORDER BY target.node_id""",
                    (
                        reference.scope.workspace_id,
                        reference.evidence_kind.value,
                        reference.evidence_id,
                        reference.revision_id,
                    ),
                ).fetchall(),
            )
        return tuple(
            EvidenceRef(
                evidence_kind=EvidenceKind(_TEXT.validate_python(row[0])),
                evidence_id=_TEXT.validate_python(row[1]),
                revision_id=_TEXT.validate_python(row[2]),
                segment_id=(
                    None if not _TEXT.validate_python(row[3]) else _TEXT.validate_python(row[3])
                ),
                scope=AccessScope.model_validate_json(_TEXT.validate_python(row[4])),
            )
            for row in rows
        )

    def _result_groups(
        self,
        actor: ActorContext,
        request: SearchRequest,
        hits: tuple[SearchHit, ...],
        now: datetime,
    ) -> tuple[tuple[SearchHit, ...], ...]:
        groups: list[tuple[SearchHit, ...]] = []
        used_citations: set[str] = set()
        used_roots: set[str] = set()
        for hit in hits:
            if hit.citation_id in used_citations:
                continue
            conflict = self._conflict_group(actor, request, hit, now)
            group = (hit,) if conflict is None else conflict
            if conflict is None and used_roots.intersection(hit.canonical_root_ids):
                continue
            groups.append(group)
            used_citations.update(item.citation_id for item in group)
            used_roots.update(root for item in group for root in item.canonical_root_ids)
        return tuple(groups)

    def _conflict_group(
        self,
        actor: ActorContext,
        request: SearchRequest,
        hit: SearchHit,
        now: datetime,
    ) -> tuple[SearchHit, ...] | None:
        if hit.claim_id is None:
            return None
        claim = self._claim_for_hit(actor.workspace_id, hit, request.historical)
        if claim is None or claim.status is not ClaimStatus.CONTESTED:
            return None
        if not claim.counter_evidence_refs:
            return None
        group_id = "conflict:" + sha256(
            "\x1f".join(
                (claim.claim_id, *(item.evidence_id for item in claim.counter_evidence_refs))
            ).encode()
        ).hexdigest()[:24]
        counters: list[SearchHit] = []
        for reference in claim.counter_evidence_refs:
            counter = self._counter_hit(
                _CounterContext(actor, hit.score, group_id, now, request.historical),
                reference,
            )
            if counter is None:
                return ()
            counters.append(counter)
        primary = _with_conflict(hit, group_id, "claim")
        return (primary, *sorted(counters, key=lambda item: (item.entity_id, item.citation_id)))

    def _claim_for_hit(
        self,
        workspace_id: str,
        hit: SearchHit,
        historical: bool,
    ) -> Claim | None:
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT version.claim_json FROM claim_versions AS version
                JOIN claim_locations AS location ON location.workspace_id=version.workspace_id
                AND location.claim_id=version.claim_id
                AND location.claim_revision_id=version.revision_id
                JOIN wiki_pages AS page ON page.workspace_id=location.workspace_id
                AND page.page_id=location.page_id
                LEFT JOIN knowledge_heads AS head ON head.workspace_id=page.workspace_id
                AND head.page_id=page.page_id
                WHERE version.workspace_id=? AND version.claim_id=? AND version.revision_id=?
                AND (? OR (location.is_current=1 AND head.revision_id=version.revision_id
                AND page.status='active')) LIMIT 1""",
                    (workspace_id, hit.claim_id, hit.revision_id, historical),
                ).fetchone(),
            )
        return None if row is None else Claim.model_validate_json(_TEXT.validate_python(row[0]))

    def _counter_hit(
        self,
        context: _CounterContext,
        reference: EvidenceRef,
    ) -> SearchHit | None:
        try:
            resolved = self._repository.resolve_evidence(context.actor, reference)
        except (AccessDeniedError, EvidenceResolutionError):
            return None
        roots = self._evidence_roots(context.actor, reference, context.now, frozenset())
        if not roots:
            return None
        match resolved:
            case Claim():
                return self._claim_counter_hit(context, reference, resolved, roots)
            case SourceSegment():
                return self._source_counter_hit(context, resolved, roots)
            case MemoryEntry():
                return SearchHit(
                    SearchCorpus.MEMORY,
                    resolved.document_id,
                    reference.revision_id,
                    resolved.entry_id,
                    resolved.document_kind.value,
                    _snippet(resolved.text),
                    context.score,
                    canonical_root_ids=roots,
                    conflict_group_id=context.group_id,
                    conflict_role="counter_evidence",
                )
            case ConversationEvent():
                return SearchHit(
                    SearchCorpus.REFERENCE,
                    resolved.message_id,
                    str(resolved.revision),
                    f"conversation:{resolved.message_id}:{resolved.revision}",
                    "conversation",
                    _snippet(resolved.text),
                    context.score,
                    canonical_root_ids=roots,
                    conflict_group_id=context.group_id,
                    conflict_role="counter_evidence",
                )

    def _claim_counter_hit(
        self,
        context: _CounterContext,
        reference: EvidenceRef,
        claim: Claim,
        roots: tuple[str, ...],
    ) -> SearchHit | None:
        probe = SearchHit(
            SearchCorpus.WIKI,
            "",
            reference.revision_id,
            f"claim:{claim.claim_id}:{reference.revision_id}",
            "",
            _snippet(claim.statement),
            context.score,
            claim_id=claim.claim_id,
            canonical_root_ids=roots,
        )
        current = self._claim_for_hit(
            context.actor.workspace_id,
            probe,
            context.historical,
        )
        if current is None:
            return None
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT location.page_id,page.page_json FROM claim_locations AS location
                JOIN wiki_pages AS page ON page.workspace_id=location.workspace_id
                AND page.page_id=location.page_id WHERE location.workspace_id=?
                AND location.claim_id=? AND location.claim_revision_id=? LIMIT 1""",
                    (context.actor.workspace_id, claim.claim_id, reference.revision_id),
                ).fetchone(),
            )
        if row is None:
            return None
        page = WikiPage.model_validate_json(_TEXT.validate_python(row[1]))
        if not _readable(context.actor, page.scope, context.now):
            return None
        return SearchHit(
            SearchCorpus.WIKI,
            _TEXT.validate_python(row[0]),
            reference.revision_id,
            f"claim:{claim.claim_id}:{reference.revision_id}",
            page.title,
            _snippet(claim.statement),
            context.score,
            claim_id=claim.claim_id,
            canonical_root_ids=roots,
            conflict_group_id=context.group_id,
            conflict_role="counter_claim",
        )

    def _source_counter_hit(
        self,
        context: _CounterContext,
        segment: SourceSegment,
        roots: tuple[str, ...],
    ) -> SearchHit | None:
        stored = self._repository.read_source(context.actor, segment.source_id)
        if stored is None or stored.source.revision_id != segment.revision_id:
            return None
        content = stored.body[segment.quote_range.start : segment.quote_range.end].decode(
            "utf-8", errors="replace"
        )
        return SearchHit(
            SearchCorpus.REFERENCE,
            segment.source_id,
            segment.revision_id,
            f"source:{segment.source_id}:{segment.revision_id}:{segment.segment_id}",
            stored.source.sanitized_locator,
            _snippet(content),
            context.score,
            canonical_root_ids=roots,
            conflict_group_id=context.group_id,
            conflict_role="counter_evidence",
        )

    def _has_pending(self, workspace_id: str) -> bool:
        with self._repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """SELECT 1 FROM index_outbox
                    WHERE workspace_id=? AND state IN ('pending','running') LIMIT 1""",
                    (workspace_id,),
                ).fetchone(),
            )
        return row is not None


def _corpus_kinds(corpus: SearchCorpus) -> tuple[str, ...]:
    match corpus:
        case SearchCorpus.REFERENCE:
            return ("source",)
        case SearchCorpus.WIKI:
            return ("page",)
        case SearchCorpus.MEMORY:
            return ("memory",)
        case SearchCorpus.ALL:
            return ("source", "page", "memory")


def _entity_corpus(entity_kind: str) -> SearchCorpus:
    match entity_kind:
        case "source":
            return SearchCorpus.REFERENCE
        case "page":
            return SearchCorpus.WIKI
        case "memory":
            return SearchCorpus.MEMORY
        case _:
            message = "invalid_chunk_entity_kind"
            raise ValueError(message)


def _fts_expression(query: str) -> str | None:
    normalized = normalized_text(query).strip()
    terms = tuple(term for term in normalized.split() if term)
    if not terms:
        return None
    korean = "".join(char for char in normalized if "가" <= char <= "힣")
    bigrams = tuple(korean[index : index + 2] for index in range(max(0, len(korean) - 1)))
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in (*terms, *bigrams))


def _query_matches(index_content: str, query: str) -> bool:
    normalized = normalized_text(query).strip()
    terms = tuple(term for term in normalized.split() if term)
    bigrams = tuple(term for term in korean_compact_bigrams(normalized).split() if term)
    return bool(terms or bigrams) and any(term in index_content for term in (*terms, *bigrams))


def _unique_candidates(candidates: tuple[_Candidate, ...]) -> tuple[_Candidate, ...]:
    seen: set[str] = set()
    unique: list[_Candidate] = []
    for candidate in candidates:
        if candidate.chunk_id in seen:
            continue
        seen.add(candidate.chunk_id)
        unique.append(candidate)
    return tuple(unique)


def _ranked(candidate: _Candidate, rank: int) -> _Candidate:
    return _Candidate(
        candidate.chunk_id,
        candidate.kind,
        candidate.entity_id,
        candidate.revision_id,
        candidate.content,
        rank,
    )


def _authorized_scores(
    visible: tuple[tuple[_Candidate, SearchHit], ...],
    vector_ranks: Mapping[str, int],
) -> tuple[SearchHit, ...]:
    lexical_rank = 0
    scored: list[SearchHit] = []
    for candidate, hit in visible:
        ranked_candidate = candidate
        if candidate.lexical_rank != 0:
            lexical_rank += 1
            ranked_candidate = _ranked(candidate, lexical_rank)
        scored.append(_with_score(hit, _rrf_score(ranked_candidate, vector_ranks)))
    return tuple(scored)


def _with_score(hit: SearchHit, score: float) -> SearchHit:
    return SearchHit(
        hit.kind,
        hit.entity_id,
        hit.revision_id,
        hit.citation_id,
        hit.title,
        hit.snippet,
        score,
        hit.related_page_ids,
        hit.needs_review,
        hit.claim_id,
        hit.canonical_root_ids,
        hit.conflict_group_id,
        hit.conflict_role,
    )


def _with_conflict(hit: SearchHit, group_id: str, role: ConflictRole) -> SearchHit:
    return SearchHit(
        hit.kind,
        hit.entity_id,
        hit.revision_id,
        hit.citation_id,
        hit.title,
        hit.snippet,
        hit.score,
        hit.related_page_ids,
        hit.needs_review,
        hit.claim_id,
        hit.canonical_root_ids,
        group_id,
        role,
    )


def _select_groups(
    groups: tuple[tuple[SearchHit, ...], ...],
    limit: int,
) -> tuple[SearchHit, ...]:
    selected: list[SearchHit] = []
    for group in groups:
        if len(selected) + len(group) > limit:
            continue
        selected.extend(group)
    return tuple(selected)


def _claim_id(chunk_id: str) -> str | None:
    marker = ":claim:"
    return None if marker not in chunk_id else chunk_id.split(marker, 1)[1]


def _readable(actor: ActorContext, scope: object, now: datetime) -> bool:
    try:
        _ = authorize_read(actor=actor, target_scope=AccessScope.model_validate(scope), at=now)
    except AccessDeniedError:
        return False
    return True


def brand_target_matches(
    kind: str, candidate_brand_id: str | None, target_brand_id: str | None
) -> bool:
    return kind != "soul" or target_brand_id is None or candidate_brand_id == target_brand_id


def _attributes_match(
    page_attributes: tuple[PageAttribute, ...], attributes: Mapping[str, str] | None
) -> bool:
    if attributes is None:
        return True
    current = {normalized_text(item.key): normalized_text(item.value) for item in page_attributes}
    return all(
        current.get(normalized_text(key)) == normalized_text(value)
        for key, value in attributes.items()
    )


def _title_terms(title: str, aliases: tuple[str, ...]) -> str:
    return normalized_text(" ".join((title, *aliases)))


def _page_metadata(
    serialized: str, historical: bool
) -> tuple[AccessScope, str, tuple[str, ...], tuple[PageAttribute, ...]]:
    if historical:
        revision = KnowledgeRevision.model_validate_json(serialized)
        return revision.scope, revision.title, revision.aliases, tuple(revision.attributes)
    page = WikiPage.model_validate_json(serialized)
    return page.scope, page.title, page.aliases, tuple(page.attributes)


def _snippet(content: str) -> str:
    return content[:_SNIPPET_LIMIT]


def _rrf_score(candidate: _Candidate, vector_ranks: Mapping[str, int]) -> float:
    score = 0 if candidate.lexical_rank == 0 else 1 / (60 + candidate.lexical_rank)
    vector_rank = vector_ranks.get(candidate.chunk_id)
    return score if vector_rank is None else score + (1 / (60 + vector_rank))


def _vector_ranks(
    candidates: tuple[VectorCandidate, ...],
    authorized_ids: frozenset[str],
) -> dict[str, int]:
    authorized = tuple(
        candidate for candidate in candidates if candidate.chunk_id in authorized_ids
    )
    return {candidate.chunk_id: rank for rank, candidate in enumerate(authorized, start=1)}


__all__ = [
    "KnowledgeRetriever",
    "SearchCorpus",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "VectorCandidate",
    "VectorSearchPort",
    "brand_target_matches",
]
