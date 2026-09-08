from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from multiprocessing import get_context
from typing import TYPE_CHECKING, Protocol

import pytest
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRequest,
    EditorialContextBlock,
    EditorialContextRole,
    KnowledgeContextTransfer,
    ValidationStage,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_selection import (
    ContextBudget,
    ContextReceipt,
    ContextRequest,
    ContextTokenCounts,
    KnowledgeActionKind,
    RetrievalStatus,
    SelectedWikiClaim,
    VoiceStatus,
)
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AuthorityClass,
    AuthorityRef,
    Brand,
    BrandEvent,
    BrandEventKind,
    BrandState,
    Claim,
    ClaimKind,
    ClaimStatus,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    IngestReceipt,
    KnowledgeJob,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeRevision,
    MemoryDocument,
    MemoryKind,
    MemoryRevision,
    OperationReceipt,
    OperationStatus,
    PageRelation,
    PageRelationKind,
    Provenance,
    QuoteRange,
    ScopeGrant,
    ScopeKind,
    SegmentLocator,
    Source,
    SourceCompleteness,
    SourceDisposition,
    SourceExtractionStatus,
    SourceKind,
    SourceSegment,
    WikiPage,
    WikiPageStatus,
)
from ads_booster.knowledge.file_store import (
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    RevisionFileDraft,
    SourceFileKind,
    SourceRevisionTarget,
)
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import (
    BrandRegistration,
    CatalogCommit,
    HeadExpectation,
    IndexOutboxItem,
    JobClaim,
    JobCompletion,
    JobRegistration,
    MembershipRole,
    PageRevisionWrite,
    RepositoryConflictError,
    SourceAdmissionChange,
    SourceObservationWrite,
    SourceRegistration,
    SqliteKnowledgeRepository,
)

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
_STRING_TUPLE: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])


class _Gate(Protocol):
    def wait(self) -> bool: ...


class _ResultQueue(Protocol):
    def put(self, item: str, /) -> None: ...

    def get(self, *, timeout: int) -> str: ...


def _commit_in_process(
    root: Path,
    command: CatalogCommit,
    gate: _Gate,
    outcomes: _ResultQueue,
) -> None:
    repository = SqliteKnowledgeRepository(root)
    _ = gate.wait()
    try:
        outcomes.put(repository.commit_catalog(command).operation_id)
    except RepositoryConflictError:
        outcomes.put("conflict")


def _scope(workspace_id: str = "workspace.a") -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_id)


def _actor(workspace_id: str = "workspace.a") -> ActorContext:
    scope = _scope(workspace_id)
    grants = tuple(
        ScopeGrant(
            grant_id=f"grant.{capability.value}.{workspace_id}",
            capability=capability,
            workspace_id=workspace_id,
            scope=scope,
            policy_epoch=1,
            effective_at=NOW,
        )
        for capability in (GrantCapability.READ, GrantCapability.WRITE)
    )
    return ActorContext(
        actor_id=f"actor.{workspace_id}",
        workspace_id=workspace_id,
        member_id=f"member.{workspace_id}",
        session_id=f"session.{workspace_id}",
        conversation_scope=scope,
        grants=grants,
        policy_epoch=1,
        authenticated_at=NOW,
    )


def _receipt(operation_id: str, revisions: tuple[str, ...]) -> OperationReceipt:
    return OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=revisions,
        retryable=False,
        occurred_at=NOW,
    )


def _page_write(
    repository: SqliteKnowledgeRepository,
    *,
    operation_id: str,
    page_id: str,
    revision_id: str,
    revision: tuple[str | None, bytes],
) -> PageRevisionWrite:
    previous_revision_id, body = revision
    digest = sha256(body).hexdigest()
    evidence = EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id="event.price",
        revision_id="event.price.rev1",
        scope=_scope(),
        provenance=Provenance.HUMAN_DIRECT,
    )
    claim = Claim(
        claim_id=f"claim.{page_id}",
        kind=ClaimKind.FACT,
        statement=body.decode(),
        status=ClaimStatus.ACTIVE,
        evidence_refs=(evidence,),
        admission_reason="Direct test evidence.",
        observed_at=NOW,
    )
    relation = PageRelation(
        relation_id=f"relation.{page_id}",
        from_page_id=page_id,
        to_page_id=page_id,
        kind=PageRelationKind.RELATED_TO,
        reason="Self relation exercises normalized relation storage.",
    )
    knowledge_revision = KnowledgeRevision(
        page_id=page_id,
        revision_id=revision_id,
        previous_revision_id=previous_revision_id,
        body_sha256=digest,
        title=page_id,
        claims=(claim,),
        relations=(relation,),
        scope=_scope(),
    )
    page = WikiPage(
        page_id=page_id,
        title=page_id,
        aliases=(f"alias {page_id}",),
        scope=_scope(),
        current_revision_id=revision_id,
        status=WikiPageStatus.ACTIVE,
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=KnowledgeRevisionTarget(page_id=page_id, revision_id=revision_id),
            content=body,
            sha256=digest,
        )
    )
    return PageRevisionWrite(
        page=page,
        revision=knowledge_revision,
        expected=HeadExpectation(
            entity_id=page_id,
            expected_revision_id=previous_revision_id,
            resulting_revision_id=revision_id,
        ),
        prepared_file=prepared,
    )


def test_brand_registration_atomically_creates_empty_soul_and_replays(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    authority = AuthorityRef(
        event_id="event.brand.register",
        authority_class=AuthorityClass.RUNTIME_POLICY,
        actor_ref=actor.actor_id,
        workspace_id=actor.workspace_id,
        scope=_scope(),
        policy_epoch=1,
    )
    brand = Brand(
        brand_id="brand.a",
        workspace_id=actor.workspace_id,
        name="Brand A",
        revision=1,
        state=BrandState.ACTIVE,
    )
    event = BrandEvent(
        event_id="event.brand.register",
        kind=BrandEventKind.REGISTERED,
        brand_id=brand.brand_id,
        workspace_id=brand.workspace_id,
        authority_ref=authority,
        occurred_at=NOW,
    )
    body = b""
    revision = MemoryRevision(
        document_id="memory.soul.brand.a",
        revision_id="memory.soul.brand.a.rev1",
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=revision.document_id,
        workspace_id=actor.workspace_id,
        kind=MemoryKind.SOUL,
        brand_id=brand.brand_id,
        timezone="UTC",
        head_revision_id=revision.revision_id,
    )
    operation_id = "operation.brand.register"
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id=actor.workspace_id,
                document_id=document.document_id,
                revision_id=revision.revision_id,
            ),
            content=body,
            sha256=revision.body_sha256,
        )
    )
    receipt = _receipt(operation_id, (revision.revision_id,))
    command = BrandRegistration(
        brand=brand,
        event=event,
        document=document,
        revision=revision,
        prepared_file=prepared,
        receipt=receipt,
        payload_sha256=contract_sha256(event),
    )

    # When
    first = repository.register_brand(command)
    replay = repository.register_brand(command)

    # Then
    assert first == replay == receipt
    assert repository.brand(actor, "brand.a") == brand
    stored = repository.read_memory(actor, document.document_id)
    assert stored is not None
    assert stored.revision == revision
    assert stored.body == b""
    assert repository.pending_memory_views(actor.workspace_id) == (revision.revision_id,)


def test_source_registration_admission_and_replay_are_one_catalog_transition(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    body = b"source body"
    source = Source(
        source_id="source.price",
        workspace_id=actor.workspace_id,
        scope=_scope(),
        owner_ref=actor.member_id,
        source_kind=SourceKind.FILE,
        sanitized_locator="price.txt",
        source_identity="upload:price.txt",
        revision_id="source.price.rev1",
        revision=1,
        fetched_at=NOW,
        sha256=sha256(body).hexdigest(),
        mime_type="text/plain",
        byte_length=len(body),
        extraction_status=SourceExtractionStatus.COMPLETE,
        completeness=SourceCompleteness.FULL,
        extractor_version="extractor.v1",
        disposition=SourceDisposition.PENDING,
        admission_revision=0,
    )
    segment = SourceSegment(
        source_id=source.source_id,
        revision_id=source.revision_id,
        segment_id="segment.price.1",
        content_sha256=source.sha256,
        extraction_version="extraction.v1",
        locator=SegmentLocator(line_start=1, line_end=1),
        quote_range=QuoteRange(start=0, end=len(body)),
        completeness=SourceCompleteness.FULL,
    )
    ingest_receipt = IngestReceipt(
        schema="knowledge.ingest-receipt.v1",
        delivery_id="delivery.price.1",
        source_id=source.source_id,
        source_revision_id=source.revision_id,
        curation_job_id="job.price.curate",
        index_operation_id="index.price.prepare",
        replayed=False,
    )
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id=ingest_receipt.curation_job_id,
        workspace_id=actor.workspace_id,
        scope=_scope(),
        kind=JobKind.CURATION,
        state=JobState.QUEUED,
        priority=JobPriority.ROUTINE,
        root_event_id="event.price",
        policy_version="policy.v1",
        due_at=NOW,
        created_at=NOW,
    )
    index = IndexOutboxItem(
        item_id=ingest_receipt.index_operation_id,
        workspace_id=actor.workspace_id,
        entity_kind="source",
        entity_id=source.source_id,
        revision_id=source.revision_id,
        extraction_version="extraction.v1",
        admission_revision=0,
        indexer_version="indexer.v1",
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id="operation.source.register",
            target=SourceRevisionTarget(
                source_id=source.source_id,
                revision_id=source.revision_id,
                file_kind=SourceFileKind.ORIGINAL,
            ),
            content=body,
            sha256=source.sha256,
        )
    )
    initial_observation = SourceObservationWrite(
        observation_id="observation.price.200",
        workspace_id=actor.workspace_id,
        source_id=source.source_id,
        revision_id=source.revision_id,
        fetched_at=NOW - timedelta(seconds=1),
        final_url="https://example.com/price.txt",
        http_status=200,
        etag='"price-v1"',
    )
    command = SourceRegistration(
        operation_id="operation.source.register",
        payload_sha256=contract_sha256(source),
        source=source,
        segments=(segment,),
        receipt=ingest_receipt,
        job=JobRegistration(job=job, unique_key="source.price.rev1:extraction.v1:policy.v1"),
        index_item=index,
        prepared_files=(prepared,),
        observation=initial_observation,
    )

    # When
    first = repository.register_source(command)
    replay = repository.register_source(command)
    admitted = repository.change_source_admission(
        SourceAdmissionChange(
            operation_id="operation.source.admit",
            payload_sha256="f" * 64,
            workspace_id=actor.workspace_id,
            source_id=source.source_id,
            expected_admission_revision=0,
            disposition=SourceDisposition.REFERENCE,
            index_item=IndexOutboxItem(
                item_id="index.price.visible",
                workspace_id=actor.workspace_id,
                entity_kind="source",
                entity_id=source.source_id,
                revision_id=source.revision_id,
                extraction_version="extraction.v1",
                admission_revision=1,
                indexer_version="indexer.v1",
            ),
            occurred_at=NOW,
        )
    )

    # Then
    assert first == ingest_receipt
    assert replay.model_copy(update={"replayed": False}) == ingest_receipt
    assert replay.replayed is True
    assert admitted.disposition is SourceDisposition.REFERENCE
    assert admitted.admission_revision == 1
    stored = repository.read_source(actor, source.source_id)
    assert stored is not None
    assert stored.body == body
    assert stored.segments == (segment,)
    assert repository.source_by_identity(actor, SourceKind.FILE, source.source_identity) == admitted
    assert repository.source_ingest_head(actor, SourceKind.FILE, source.source_identity) == admitted
    assert repository.latest_source_observation(actor, source.source_id) == initial_observation
    observation = SourceObservationWrite(
        observation_id="observation.price.304",
        workspace_id=actor.workspace_id,
        source_id=source.source_id,
        revision_id=source.revision_id,
        fetched_at=NOW,
        final_url="https://example.com/price.txt",
        http_status=304,
        etag='"price-v1"',
    )
    assert repository.record_source_observation(actor, observation) is True
    assert repository.record_source_observation(actor, observation) is False
    assert repository.latest_source_observation(actor, source.source_id) == observation
    assert repository.pending_index_items(actor.workspace_id) == (
        "index.price.prepare",
        "index.price.visible",
    )


def test_atomic_multi_head_cas_has_one_winner_and_complete_orphans(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    initial_writes = tuple(
        _page_write(
            repository,
            operation_id="operation.pages.initial",
            page_id=page_id,
            revision_id=f"{page_id}.rev1",
            revision=(None, b"initial"),
        )
        for page_id in ("page.a", "page.b")
    )
    initial_operation = KnowledgeOperation(
        operation_id="operation.pages.initial",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=("page.a", "page.b"),
        expected_revision_ids=("none", "none"),
        reason="Create both pages.",
    )
    _ = repository.commit_catalog(
        CatalogCommit(
            operation_id=initial_operation.operation_id,
            actor=actor,
            payload_sha256=contract_sha256(initial_operation),
            receipt=_receipt(
                initial_operation.operation_id,
                tuple(write.revision.revision_id for write in initial_writes),
            ),
            operation_records=(initial_operation,),
            page_writes=initial_writes,
        )
    )

    def command(suffix: str) -> CatalogCommit:
        operation_id = f"operation.pages.{suffix}"
        writes = tuple(
            _page_write(
                repository,
                operation_id=operation_id,
                page_id=page_id,
                revision_id=f"{page_id}.rev2.{suffix}",
                revision=(f"{page_id}.rev1", f"winner {suffix}".encode()),
            )
            for page_id in ("page.a", "page.b")
        )
        operation = KnowledgeOperation(
            operation_id=operation_id,
            kind=KnowledgeOperationKind.PAGE_EDIT,
            target_page_ids=("page.a", "page.b"),
            expected_revision_ids=("page.a.rev1", "page.b.rev1"),
            reason=f"Concurrent edit {suffix}.",
        )
        return CatalogCommit(
            operation_id=operation_id,
            actor=actor,
            payload_sha256=contract_sha256(operation),
            receipt=_receipt(operation_id, tuple(item.revision.revision_id for item in writes)),
            operation_records=(operation,),
            page_writes=writes,
        )

    commands = (command("left"), command("right"))

    # When
    process_context = get_context("fork")
    gate = process_context.Event()
    outcome_queue: _ResultQueue = process_context.Queue()
    processes = tuple(
        process_context.Process(
            target=_commit_in_process,
            args=(repository.root, command, gate, outcome_queue),
        )
        for command in commands
    )
    for process in processes:
        process.start()
    gate.set()
    outcomes = _STRING_TUPLE.validate_python(
        tuple(outcome_queue.get(timeout=10) for _ in processes)
    )
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    # Then
    assert outcomes.count("conflict") == 1
    winner = next(item for item in outcomes if item != "conflict").rsplit(".", 1)[-1]
    assert repository.page_head(actor, "page.a") == f"page.a.rev2.{winner}"
    assert repository.page_head(actor, "page.b") == f"page.b.rev2.{winner}"
    loser = "right" if winner == "left" else "left"
    assert repository.files.published_exists(
        KnowledgeRevisionTarget(page_id="page.a", revision_id=f"page.a.rev2.{loser}")
    )
    assert repository.files.published_exists(
        KnowledgeRevisionTarget(page_id="page.b", revision_id=f"page.b.rev2.{loser}")
    )
    assert repository.operation_receipt(actor, f"operation.pages.{loser}") is None


def test_jobs_are_generation_fenced_and_transfer_dependencies_are_normalized(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id="job.lease",
        workspace_id=actor.workspace_id,
        scope=_scope(),
        kind=JobKind.INDEX,
        state=JobState.QUEUED,
        priority=JobPriority.URGENT,
        root_event_id="event.lease",
        policy_version="policy.v1",
        due_at=NOW,
        created_at=NOW,
    )
    repository.put_job(JobRegistration(job=job, unique_key="job:lease"))
    lease = repository.claim_job(
        JobClaim(
            worker_id="worker.a",
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
        )
    )
    assert lease is not None

    request = ContextRequest(
        schema="knowledge.context-request.v1",
        request_id="request.transfer",
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        task_ref="task.transfer",
        brand_ref=None,
        query="Write the price page.",
        required_context=True,
        budget=ContextBudget(max_input_tokens=4_000),
    )
    selected = SelectedWikiClaim(
        page_id="page.a",
        revision_id="page.a.rev1",
        claim_ids=("claim.page.a",),
        content_sha256="a" * 64,
    )
    receipt = ContextReceipt(
        schema="knowledge.context-receipt.v1",
        receipt_id="receipt.transfer",
        task_ref=request.task_ref,
        scoped_actor_ref=actor.actor_id,
        team_id=actor.workspace_id,
        policy_version="policy.v1",
        action_kind=request.action_kind,
        resolved_brand_ref=None,
        voice_status=VoiceStatus.VOICE_UNCONFIGURED,
        selected_wiki_claims=(selected,),
        token_counts=ContextTokenCounts(
            required_tokens=10,
            selected_reference_tokens=5,
            total_input_tokens=15,
        ),
        retrieval_status=RetrievalStatus.READY,
        created_at=NOW,
    )
    transfer = KnowledgeContextTransfer(
        schema="trace.knowledge-context.v1",
        transfer_id="transfer.a",
        workspace_id=actor.workspace_id,
        account_id="account.a",
        scoped_actor_ref=actor.actor_id,
        brand_ref=None,
        action_kind=request.action_kind,
        run_ref="run.a",
        task_ref=request.task_ref,
        invocation_ref="invocation.a",
        request=request,
        receipt=receipt,
        editorial_context=(
            EditorialContextBlock(
                block_id="block.a",
                role=EditorialContextRole.REFERENCE,
                text="Price reference.",
                revision_refs=(selected.revision_id,),
            ),
        ),
        policy_revision="policy.v1",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )

    # When
    inserted = repository.record_context_transfer(transfer)
    replayed = repository.record_context_transfer(transfer)
    validation_request = ContextTransferValidationRequest(
        schema="trace.knowledge-context-validation-request.v1",
        request_id="validation.a",
        principal_id="hosted.service",
        stage=ValidationStage.PRE_DISPATCH,
        transfer_id=transfer.transfer_id,
        workspace_id=transfer.workspace_id,
        account_id=transfer.account_id,
        knowledge_context_sha256=knowledge_context_sha256(transfer),
    )
    validation = ContextTransferValidationAccepted(
        schema="trace.knowledge-context-validation-result.v1",
        status="accepted",
        request_id=validation_request.request_id,
        principal_id=validation_request.principal_id,
        stage=validation_request.stage,
        transfer_id=transfer.transfer_id,
        workspace_id=transfer.workspace_id,
        account_id=transfer.account_id,
        knowledge_context_sha256=validation_request.knowledge_context_sha256,
        dependency_set_sha256="d" * 64,
        checked_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
    )
    repository.record_transfer_validation(validation_request, validation)
    repository.finish_job(
        JobCompletion(
            job_id=job.job_id,
            worker_id="worker.a",
            lease_generation=lease.lease_generation,
            state=JobState.COMPLETED,
            result_sha256="b" * 64,
            completed_at=NOW + timedelta(seconds=1),
        )
    )

    # Then
    assert inserted is True
    assert replayed is False
    assert repository.transfer_dependencies(transfer.transfer_id) == (
        ("wiki_claim", selected.page_id, selected.revision_id),
    )
    assert repository.context_transfer(transfer.transfer_id) == transfer
    with pytest.raises(RepositoryConflictError, match="job_lease_stale"):
        repository.finish_job(
            JobCompletion(
                job_id=job.job_id,
                worker_id="worker.a",
                lease_generation=lease.lease_generation,
                state=JobState.FAILED,
                result_sha256="c" * 64,
                completed_at=NOW + timedelta(seconds=2),
            )
        )
