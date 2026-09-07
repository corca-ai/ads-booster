from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast, override

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.contracts.knowledge_selection import RetrievalStatus
from ads_booster.knowledge.changes import ChangeGroup, ChangePublisher
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    ClaimStatus,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    EvidenceKind,
    GrantCapability,
    IngestEnvelope,
    InstructionAuthority,
    KnowledgeOperation,
    KnowledgeOperationKind,
    MessageEventRef,
    PageRelation,
    PageRelationKind,
    Provenance,
    ScopeGrant,
    ScopeKind,
    SourceDisposition,
)
from ads_booster.knowledge.file_paths import KnowledgeRevisionTarget
from ads_booster.knowledge.indexing import KnowledgeIndexWorker
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.pages import PageChangeSet, PageSnapshot, merge_pages
from ads_booster.knowledge.repository import (
    IndexOutboxItem,
    MembershipRole,
    SourceAdmissionChange,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.retrieval import (
    KnowledgeRetriever,
    SearchCorpus,
    SearchRequest,
    VectorCandidate,
    VectorSearchPort,
    brand_target_matches,
)
from tests.knowledge.change_test_fixtures import (
    actor as catalog_actor,
)
from tests.knowledge.change_test_fixtures import (
    claim,
    digest,
    evidence,
    page_snapshot,
    register_evidence_source,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _actor(*, readable: bool = True) -> ActorContext:
    scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.a")
    capabilities = (GrantCapability.READ, GrantCapability.WRITE) if readable else ()
    return ActorContext(
        actor_id="member.a" if readable else "member.denied",
        workspace_id="workspace.a",
        member_id="member.a" if readable else "member.denied",
        session_id="session.a" if readable else "session.denied",
        conversation_scope=scope,
        grants=tuple(
            ScopeGrant(
                grant_id=f"grant.{capability.value}",
                capability=capability,
                workspace_id="workspace.a",
                scope=scope,
                policy_epoch=1,
                effective_at=NOW,
            )
            for capability in capabilities
        ),
        policy_epoch=1,
        authenticated_at=NOW,
    )


def _visible_source(repository: SqliteKnowledgeRepository, actor: ActorContext) -> str:
    event = ConversationEvent(
        conversation_id="conversation.a",
        message_id="message.a",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=actor.member_id,
        created_at=NOW,
        text="출시 가격은 31,000원입니다.",
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=actor.conversation_scope,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.retrieval.1",
        event_kind=event.event_kind,
        request_text=event.text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id,
            message_ref=event.message_id,
            revision=event.revision,
        ),
        timestamp=NOW,
    )
    delivery_receipt = KnowledgeIngestion(repository).ingest(actor, event, envelope)
    receipt = delivery_receipt.unit_receipts[0].receipt
    _ = repository.change_source_admission(
        SourceAdmissionChange(
            operation_id="operation.retrieval.admit",
            payload_sha256=sha256(b"retrieval admission").hexdigest(),
            workspace_id=actor.workspace_id,
            source_id=receipt.source_id,
            expected_admission_revision=0,
            disposition=SourceDisposition.REFERENCE,
            index_item=IndexOutboxItem(
                item_id="index.retrieval.visible",
                workspace_id=actor.workspace_id,
                entity_kind="source",
                entity_id=receipt.source_id,
                revision_id=receipt.source_revision_id,
                extraction_version="extractor.v1",
                admission_revision=1,
            ),
            occurred_at=NOW,
        )
    )
    return receipt.source_id


def _indexed_repository(tmp_path: Path) -> tuple[SqliteKnowledgeRepository, ActorContext, str]:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    source_id = _visible_source(repository, actor)
    worker = KnowledgeIndexWorker(repository)
    assert worker.run_once(actor.workspace_id, "worker.a", NOW).state == "waiting"
    assert (
        worker.run_once(actor.workspace_id, "worker.a", NOW + timedelta(seconds=1)).state
        == "indexed"
    )
    return repository, actor, source_id


def test_source_index_waits_for_admission_then_finds_compact_korean_query(tmp_path: Path) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)

    result = KnowledgeRetriever(repository).search(
        actor, SearchRequest(query="출시가격", corpus=SearchCorpus.REFERENCE), now=NOW
    )

    assert [(hit.entity_id, hit.kind) for hit in result.hits] == [
        (source_id, SearchCorpus.REFERENCE)
    ]
    assert result.hits[0].citation_id.startswith(f"source:{source_id}:")
    assert result.status is RetrievalStatus.READY
    assert result.index_pending is False
    assert result.total_count == 1


def test_retrieval_rechecks_acl_and_visibility_after_indexing(tmp_path: Path) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)

    denied = KnowledgeRetriever(repository).search(
        _actor(readable=False), SearchRequest(query="가격"), now=NOW
    )
    with repository.connection() as connection:
        _ = connection.execute(
            "UPDATE sources SET visibility='blocked' WHERE workspace_id=? AND source_id=?",
            (actor.workspace_id, source_id),
        )
    blocked = KnowledgeRetriever(repository).search(actor, SearchRequest(query="가격"), now=NOW)

    assert denied.hits == ()
    assert denied.total_count == 0
    assert blocked.hits == ()
    assert blocked.total_count == 0


@dataclass(frozen=True, slots=True)
class FailingVectorPort(VectorSearchPort):
    @override
    def search(self, query: str, limit: int) -> tuple[VectorCandidate, ...]:
        raise RuntimeError


@dataclass(frozen=True, slots=True)
class StaticVectorPort(VectorSearchPort):
    candidates: tuple[VectorCandidate, ...]

    @override
    def search(self, query: str, limit: int) -> tuple[VectorCandidate, ...]:
        return self.candidates[:limit]


def test_vector_failure_returns_lexical_hits_with_degraded_status(tmp_path: Path) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)

    result = KnowledgeRetriever(repository, FailingVectorPort()).search(
        actor, SearchRequest(query="가격"), now=NOW
    )

    assert [hit.entity_id for hit in result.hits] == [source_id]
    assert result.status is RetrievalStatus.DEGRADED


def test_vector_only_candidate_is_authorized_before_rank_and_returned(tmp_path: Path) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)
    with repository.connection() as connection:
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT chunk_id FROM chunks WHERE workspace_id=? AND entity_id=? LIMIT 1",
                (actor.workspace_id, source_id),
            ).fetchone(),
        )
    assert row is not None
    vector = StaticVectorPort((VectorCandidate(str(row[0]), 0.9),))

    allowed = KnowledgeRetriever(repository, vector).search(
        actor, SearchRequest(query="semantic-only-query"), now=NOW
    )
    denied = KnowledgeRetriever(repository, vector).search(
        _actor(readable=False), SearchRequest(query="semantic-only-query"), now=NOW
    )

    assert [hit.entity_id for hit in allowed.hits] == [source_id]
    assert allowed.status is RetrievalStatus.READY
    assert denied.hits == ()
    assert denied.total_count == 0


def test_expired_index_lease_is_reclaimed_without_duplicate_chunks(tmp_path: Path) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)
    with repository.connection() as connection:
        _ = connection.execute(
            """UPDATE index_outbox
            SET state='running',lease_owner='crashed.worker',lease_expires_at=?
            WHERE item_id='index.retrieval.visible'""",
            ((NOW - timedelta(seconds=1)).isoformat(),),
        )

    result = KnowledgeIndexWorker(repository).run_once(actor.workspace_id, "recovery.worker", NOW)
    with repository.connection() as connection:
        rows = connection.execute(
            "SELECT chunk_id FROM chunks WHERE workspace_id=? AND entity_id=?",
            (actor.workspace_id, source_id),
        ).fetchall()

    assert result.state == "indexed"
    assert len(rows) == 1


def test_historical_source_chunks_remain_hidden_by_default_and_available_explicitly(
    tmp_path: Path,
) -> None:
    repository, actor, source_id = _indexed_repository(tmp_path)
    updated_at = NOW + timedelta(minutes=1)
    edited = ConversationEvent(
        conversation_id="conversation.a",
        message_id="message.a",
        revision=2,
        sequence=2,
        role=ConversationRole.USER,
        speaker_ref=actor.member_id,
        created_at=NOW,
        edited_at=updated_at,
        text="출시 가격은 32,000원입니다.",
        event_kind=ConversationEventKind.MESSAGE_EDITED,
        scope=actor.conversation_scope,
    )
    delivery_receipt = KnowledgeIngestion(repository).ingest(
        actor,
        edited,
        IngestEnvelope(
            schema="knowledge.ingest-envelope.v1",
            delivery_id="delivery.retrieval.2",
            event_kind=edited.event_kind,
            request_text=edited.text,
            message_event=MessageEventRef(
                conversation_ref=edited.conversation_id,
                message_ref=edited.message_id,
                revision=edited.revision,
            ),
            timestamp=updated_at,
        ),
    )
    receipt = delivery_receipt.unit_receipts[0].receipt
    _ = repository.change_source_admission(
        SourceAdmissionChange(
            operation_id="operation.retrieval.admit.2",
            payload_sha256=sha256(b"retrieval admission 2").hexdigest(),
            workspace_id=actor.workspace_id,
            source_id=source_id,
            expected_admission_revision=0,
            disposition=SourceDisposition.REFERENCE,
            index_item=IndexOutboxItem(
                item_id="index.retrieval.visible.2",
                workspace_id=actor.workspace_id,
                entity_kind="source",
                entity_id=source_id,
                revision_id=receipt.source_revision_id,
                extraction_version="extractor.v1",
                admission_revision=1,
            ),
            occurred_at=NOW + timedelta(minutes=1),
        )
    )
    worker = KnowledgeIndexWorker(repository)
    assert (
        worker.run_once(actor.workspace_id, "worker.a", NOW + timedelta(minutes=1)).state
        == "waiting"
    )
    assert (
        worker.run_once(actor.workspace_id, "worker.a", NOW + timedelta(minutes=2)).state
        == "indexed"
    )

    current = KnowledgeRetriever(repository).search(
        actor, SearchRequest(query="출시 가격"), now=NOW
    )
    historical = KnowledgeRetriever(repository).search(
        actor, SearchRequest(query="출시 가격", historical=True), now=NOW
    )

    assert [hit.revision_id for hit in current.hits] == [receipt.source_revision_id]
    assert len(historical.hits) == 2


def test_general_research_allows_authorized_soul_and_target_filter_excludes_other_brand() -> None:
    assert brand_target_matches("soul", "brand.a", None)
    assert brand_target_matches("soul", "brand.a", "brand.a")
    assert not brand_target_matches("soul", "brand.b", "brand.a")


def _publish_page(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    snapshot: PageSnapshot,
    operation_id: str,
) -> None:
    operation = KnowledgeOperation(
        operation_id=operation_id,
        kind=(
            KnowledgeOperationKind.PAGE_CREATE
            if snapshot.revision.previous_revision_id is None
            else KnowledgeOperationKind.PAGE_EDIT
        ),
        target_page_ids=(snapshot.page.page_id,),
        expected_revision_ids=(snapshot.revision.previous_revision_id or "none",),
        claim_ids=tuple(item.claim_id for item in snapshot.revision.claims),
        evidence_refs=tuple(
            reference.evidence_id
            for item in snapshot.revision.claims
            for reference in (*item.evidence_refs, *item.counter_evidence_refs)
        ),
        reason="Publish a retrieval fixture page.",
    )
    _ = ChangePublisher(repository).publish(
        actor=actor,
        group=ChangeGroup(operation_id=operation_id, page_operation=operation),
        pages=PageChangeSet(current={snapshot.page.page_id: snapshot}, redirects={}),
        memories=(),
        at=NOW,
    )


def _drain_index(repository: SqliteKnowledgeRepository, workspace_id: str) -> None:
    worker = KnowledgeIndexWorker(repository)
    while worker.run_once(workspace_id, "worker.retrieval", NOW).state != "idle":
        pass


def test_explicit_counter_claims_form_an_atomic_conflict_group(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source_ref, _ = register_evidence_source(repository, body=b"The evidence record is disputed.")
    opposed = claim(
        claim_id="claim.opposed",
        statement="The verified launch price is 39 dollars.",
        evidence_ref=source_ref,
    )
    opposed_page = PageSnapshot(
        *page_snapshot(
            page_id="page.opposed",
            title="Opposed price",
            revision_id="page.opposed.r1",
            claims=(opposed,),
        )
    )
    _publish_page(repository, editor, opposed_page, "operation.opposed")
    counter_ref = evidence(
        evidence_id=opposed.claim_id,
        revision_id=opposed_page.revision.revision_id,
        kind=EvidenceKind.CLAIM,
        quote=opposed.statement,
    ).model_copy(
        update={
            "segment_id": None,
            "quote_sha256": None,
            "instruction_authority": InstructionAuthority.DATA,
            "provenance": Provenance.AGENT_DERIVED,
        }
    )
    contested = claim(
        claim_id="claim.contested",
        statement="반박대상 launch price is 29 dollars.",
        evidence_ref=source_ref,
    ).model_copy(
        update={
            "status": ClaimStatus.CONTESTED,
            "counter_evidence_refs": (counter_ref,),
        }
    )
    contested_page = PageSnapshot(
        *page_snapshot(
            page_id="page.contested",
            title="Contested price",
            revision_id="page.contested.r1",
            claims=(contested,),
        )
    )
    _publish_page(repository, editor, contested_page, "operation.contested")
    _drain_index(repository, editor.workspace_id)

    grouped = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="반박대상", limit=2), now=NOW
    )
    too_small = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="반박대상", limit=1), now=NOW
    )

    assert [hit.claim_id for hit in grouped.hits] == ["claim.contested", "claim.opposed"]
    assert {hit.conflict_role for hit in grouped.hits} == {"claim", "counter_claim"}
    assert len({hit.conflict_group_id for hit in grouped.hits}) == 1
    assert grouped.total_count == 2
    assert too_small.hits == ()
    assert too_small.total_count == 2


def test_source_counter_evidence_stays_an_evidence_hit_without_fabricated_claim(
    tmp_path: Path,
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source_ref, _ = register_evidence_source(repository, body=b"The source records 39 dollars.")
    contested = claim(
        claim_id="claim.source-counter",
        statement="sourcecounter says the price is 29 dollars.",
        evidence_ref=source_ref,
    ).model_copy(
        update={
            "status": ClaimStatus.CONTESTED,
            "counter_evidence_refs": (source_ref,),
        }
    )
    snapshot = PageSnapshot(*page_snapshot(claims=(contested,)))
    _publish_page(repository, editor, snapshot, "operation.source-counter")
    _drain_index(repository, editor.workspace_id)

    result = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="sourcecounter", limit=2), now=NOW
    )

    assert [hit.kind for hit in result.hits] == [SearchCorpus.WIKI, SearchCorpus.REFERENCE]
    assert result.hits[1].claim_id is None
    assert result.hits[1].conflict_role == "counter_evidence"


def test_common_canonical_source_deduplicates_claims_without_creating_conflict(
    tmp_path: Path,
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source_ref, _ = register_evidence_source(repository, body=b"sharedroot evidence")
    for ordinal in range(2):
        current_claim = claim(
            claim_id=f"claim.shared.{ordinal}",
            statement=f"sharedroot derived statement {ordinal}",
            evidence_ref=source_ref,
        )
        page, revision, body = page_snapshot(
            page_id=f"page.shared.{ordinal}",
            title=f"Shared {ordinal}",
            revision_id=f"page.shared.{ordinal}.r1",
            claims=(current_claim,),
        )
        _publish_page(
            repository,
            editor,
            PageSnapshot(page, revision, body),
            f"operation.shared.{ordinal}",
        )
    _drain_index(repository, editor.workspace_id)

    result = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="sharedroot", limit=8), now=NOW
    )

    assert len(result.hits) == 1
    assert result.hits[0].conflict_group_id is None
    assert result.hits[0].canonical_root_ids == (
        f"source:{source_ref.evidence_id}:{source_ref.revision_id}:{digest('sharedroot evidence')}",
    )


def test_stale_index_fallback_scans_at_most_one_hundred_current_entities(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    for ordinal in range(101):
        body = f"boundedfallback page {ordinal}".encode()
        page, revision, _ = page_snapshot(
            page_id=f"page.pending.{ordinal:03d}",
            title=f"Pending {ordinal:03d}",
            revision_id=f"page.pending.{ordinal:03d}.r1",
            claims=(),
        )
        snapshot = PageSnapshot(
            page,
            revision.model_copy(
                update={"body_sha256": sha256(body).hexdigest(), "claims": ()}
            ),
            body,
        )
        _publish_page(repository, editor, snapshot, f"operation.pending.{ordinal:03d}")

    result = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="boundedfallback", corpus=SearchCorpus.WIKI, limit=20), now=NOW
    )

    assert len(result.hits) == 20
    assert result.total_count == 100
    assert result.index_pending is True
    assert result.status is RetrievalStatus.INDEX_PENDING


def test_stale_index_fallback_fails_closed_when_authoritative_file_is_missing(
    tmp_path: Path,
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    body = b"missingfallback body"
    page, revision, _ = page_snapshot(claims=())
    snapshot = PageSnapshot(
        page,
        revision.model_copy(update={"body_sha256": sha256(body).hexdigest(), "claims": ()}),
        body,
    )
    _publish_page(repository, editor, snapshot, "operation.missing-fallback")
    stored = repository.read_page(editor, page.page_id)
    assert stored is not None
    relative_path = repository.files.relative_path(
        KnowledgeRevisionTarget(page.page_id, revision.revision_id)
    )
    (repository.root / relative_path).unlink()

    result = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="missingfallback", corpus=SearchCorpus.WIKI), now=NOW
    )

    assert result.hits == ()
    assert result.total_count == 0
    assert result.status is RetrievalStatus.DEGRADED


def test_pending_fallback_uses_only_current_head(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    page, revision, _ = page_snapshot(claims=())
    first_body = b"oldpendingmarker"
    first = PageSnapshot(
        page,
        revision.model_copy(
            update={"body_sha256": sha256(first_body).hexdigest(), "claims": ()}
        ),
        first_body,
    )
    _publish_page(repository, editor, first, "operation.pending-head.r1")
    second_body = b"newpendingmarker"
    second_revision = revision.model_copy(
        update={
            "revision_id": "page.pricing.r2",
            "previous_revision_id": revision.revision_id,
            "body_sha256": sha256(second_body).hexdigest(),
            "claims": (),
        }
    )
    second = PageSnapshot(
        page.model_copy(update={"current_revision_id": second_revision.revision_id}),
        second_revision,
        second_body,
    )
    _publish_page(repository, editor, second, "operation.pending-head.r2")

    old = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="oldpendingmarker", corpus=SearchCorpus.WIKI), now=NOW
    )
    current = KnowledgeRetriever(repository).search(
        editor, SearchRequest(query="newpendingmarker", corpus=SearchCorpus.WIKI), now=NOW
    )

    assert old.hits == ()
    assert [hit.revision_id for hit in current.hits] == ["page.pricing.r2"]


def test_related_page_ids_apply_target_acl_before_one_hop_cap(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    public_actor = catalog_actor()
    private_scope = AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id="workspace.alpha",
        member_id="member.editor",
        session_id="session.editor",
    )
    private_actor = catalog_actor(scope=private_scope)
    repository.register_actor(public_actor, MembershipRole.ADMIN)
    repository.register_actor(private_actor, MembershipRole.ADMIN)
    private_page, private_revision, private_body = page_snapshot(
        page_id="page.private",
        title="PRIVATE_CANARY_TITLE",
        revision_id="page.private.r1",
        claims=(),
    )
    private = PageSnapshot(
        private_page.model_copy(update={"scope": private_scope}),
        private_revision.model_copy(update={"scope": private_scope, "claims": ()}),
        private_body,
    )
    _publish_page(repository, private_actor, private, "operation.private-page")
    relation = PageRelation(
        relation_id="relation.public.private",
        from_page_id="page.public",
        to_page_id=private.page.page_id,
        kind=PageRelationKind.RELATED_TO,
        reason="A private neighboring topic.",
    )
    public_body = b"publicpointer body"
    public_page, public_revision, _ = page_snapshot(
        page_id="page.public",
        title="Public page",
        revision_id="page.public.r1",
        claims=(),
    )
    public = PageSnapshot(
        public_page,
        public_revision.model_copy(
            update={
                "body_sha256": sha256(public_body).hexdigest(),
                "claims": (),
                "relations": (relation,),
            }
        ),
        public_body,
    )
    _publish_page(repository, public_actor, public, "operation.public-page")
    _drain_index(repository, public_actor.workspace_id)

    result = KnowledgeRetriever(repository).search(
        public_actor,
        SearchRequest(query="publicpointer", corpus=SearchCorpus.WIKI),
        now=NOW,
    )

    assert len(result.hits) == 1
    assert result.hits[0].related_page_ids == ()
    assert "PRIVATE_CANARY_TITLE" not in result.hits[0].snippet


def test_redirected_page_search_returns_current_target_without_old_pointer_text(
    tmp_path: Path,
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = catalog_actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    target_page, target_revision, _ = page_snapshot(claims=())
    target_body = b"current target body"
    target = PageSnapshot(
        target_page,
        target_revision.model_copy(
            update={"body_sha256": sha256(target_body).hexdigest(), "claims": ()}
        ),
        target_body,
    )
    source_page, source_revision, source_body = page_snapshot(
        page_id="page.legacy",
        title="legacyredirectmarker",
        revision_id="page.legacy.r1",
        claims=(),
    )
    source = PageSnapshot(
        source_page,
        source_revision.model_copy(update={"claims": ()}),
        source_body,
    )
    create = KnowledgeOperation(
        operation_id="operation.redirect.create",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(target.page.page_id, source.page.page_id),
        expected_revision_ids=("none", "none"),
        reason="Create redirect fixtures.",
    )
    publisher = ChangePublisher(repository)
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create.operation_id, page_operation=create),
        pages=PageChangeSet(
            current={target.page.page_id: target, source.page.page_id: source},
            redirects={},
        ),
        memories=(),
        at=NOW,
    )
    _drain_index(repository, editor.workspace_id)
    merged = merge_pages(
        target=target,
        sources=(source,),
        claim_target_map={},
        revision_ids={
            target.page.page_id: "page.pricing.r2",
            source.page.page_id: "page.legacy.r2",
        },
        reason="Merge the legacy page.",
    )
    merge = KnowledgeOperation(
        operation_id="operation.redirect.merge",
        kind=KnowledgeOperationKind.PAGE_MERGE,
        target_page_ids=(target.page.page_id, source.page.page_id),
        expected_revision_ids=(target.revision.revision_id, source.revision.revision_id),
        reason="Merge the legacy page.",
    )
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=merge.operation_id, page_operation=merge),
        pages=merged,
        memories=(),
        at=NOW,
    )

    result = KnowledgeRetriever(repository).search(
        editor,
        SearchRequest(query="legacyredirectmarker", corpus=SearchCorpus.WIKI),
        now=NOW,
    )

    assert [hit.entity_id for hit in result.hits] == [target.page.page_id]
    assert result.hits[0].title == target.page.title
    assert "legacyredirectmarker" not in result.hits[0].snippet
