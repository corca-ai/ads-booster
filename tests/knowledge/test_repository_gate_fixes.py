from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event, Thread
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
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
    DependencyState,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    KnowledgeRevision,
    MemoryDocument,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryRevision,
    MemoryStatus,
    OperationReceipt,
    OperationStatus,
    ScopeGrant,
    ScopeKind,
    UsageRole,
    WikiPage,
    WikiPageStatus,
)
from ads_booster.knowledge.errors import EvidenceResolutionError, PolicyEpochStaleError
from ads_booster.knowledge.file_store import (
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    RevisionFileDraft,
)
from ads_booster.knowledge.repository import (
    BrandRegistration,
    CatalogCommit,
    EvidenceDependencyInvalidation,
    HeadExpectation,
    MembershipRole,
    MemoryRevisionWrite,
    PageRevisionWrite,
    RepositoryCommitBoundary,
    RepositoryConflictError,
    RunBinding,
    RunBindingState,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.retrieval import KnowledgeRetriever, SearchCorpus, SearchRequest

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_OPTIONAL_COUNT_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)


class _InjectedCrashError(RuntimeError):
    pass


def _scope(workspace_id: str) -> AccessScope:
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


def _receipt(operation_id: str, revision_id: str) -> OperationReceipt:
    return OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=(revision_id,),
        retryable=False,
        occurred_at=NOW,
    )


def _brand_registration(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    brand_id: str,
    suffix: str,
) -> BrandRegistration:
    operation_id = f"operation.brand.{suffix}"
    document_id = f"memory.soul.{suffix}"
    revision_id = f"{document_id}.rev1"
    brand = Brand(
        brand_id=brand_id,
        workspace_id=actor.workspace_id,
        name=f"Brand {suffix}",
        revision=1,
        state=BrandState.ACTIVE,
    )
    authority = AuthorityRef(
        event_id=f"event.brand.{suffix}",
        authority_class=AuthorityClass.RUNTIME_POLICY,
        actor_ref=actor.actor_id,
        workspace_id=actor.workspace_id,
        scope=actor.conversation_scope,
        policy_epoch=actor.policy_epoch,
    )
    event = BrandEvent(
        event_id=authority.event_id,
        kind=BrandEventKind.REGISTERED,
        brand_id=brand_id,
        workspace_id=actor.workspace_id,
        authority_ref=authority,
        occurred_at=NOW,
    )
    body = f"immutable {brand_id}".encode()
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=actor.workspace_id,
        kind=MemoryKind.SOUL,
        brand_id=brand_id,
        timezone="UTC",
        head_revision_id=revision_id,
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id=actor.workspace_id,
                document_id=document_id,
                revision_id=revision_id,
            ),
            content=body,
            sha256=revision.body_sha256,
        )
    )
    return BrandRegistration(
        brand=brand,
        event=event,
        document=document,
        revision=revision,
        prepared_file=prepared,
        receipt=_receipt(operation_id, revision_id),
        payload_sha256=contract_sha256(event),
    )


def _memory_write(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    *,
    operation_id: str,
    document_id: str,
    brand_id: str,
) -> MemoryRevisionWrite:
    revision_id = f"{document_id}.rev1"
    body = f"duplicate {brand_id}".encode()
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=actor.workspace_id,
        kind=MemoryKind.SOUL,
        brand_id=brand_id,
        timezone="UTC",
        head_revision_id=revision_id,
    )
    return MemoryRevisionWrite(
        document=document,
        revision=revision,
        expected=HeadExpectation(document_id, None, revision_id),
        prepared_file=repository.files.prepare(
            RevisionFileDraft(
                operation_id=operation_id,
                target=MemoryRevisionTarget(
                    workspace_id=actor.workspace_id,
                    document_id=document_id,
                    revision_id=revision_id,
                ),
                content=body,
                sha256=revision.body_sha256,
            )
        ),
    )


def _memory_entry_write(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
) -> tuple[MemoryRevisionWrite, MemoryEntry]:
    operation_id = "operation.memory.upstream"
    document_id = "memory.team.upstream"
    revision_id = f"{document_id}.rev1"
    body = b"Team upstream evidence."
    entry = MemoryEntry(
        entry_id="memory.entry.upstream",
        document_id=document_id,
        document_kind=MemoryKind.TEAM,
        text="Team upstream evidence.",
        kind=MemoryEntryKind.FACT,
        status=MemoryStatus.ACTIVE,
        dependency_state=DependencyState.CURRENT,
        origin=MemoryOrigin.DIRECT,
        usage_role=UsageRole.REFERENCE,
        scope=actor.conversation_scope,
        source_refs=(
            EvidenceRef(
                evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                evidence_id="event.memory.upstream",
                revision_id="1",
                scope=actor.conversation_scope,
            ),
        ),
        admission_reason="Fixture for reverse dependency invalidation.",
    )
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        entry_ids=(entry.entry_id,),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=actor.workspace_id,
        kind=MemoryKind.TEAM,
        timezone="UTC",
        head_revision_id=revision_id,
    )
    return (
        MemoryRevisionWrite(
            document=document,
            revision=revision,
            expected=HeadExpectation(document_id, None, revision_id),
            prepared_file=repository.files.prepare(
                RevisionFileDraft(
                    operation_id=operation_id,
                    target=MemoryRevisionTarget(
                        workspace_id=actor.workspace_id,
                        document_id=document_id,
                        revision_id=revision_id,
                    ),
                    content=body,
                    sha256=revision.body_sha256,
                )
            ),
            entries=(entry,),
        ),
        entry,
    )


def _dependent_page_commit(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    evidence: EvidenceRef,
    suffix: str,
) -> CatalogCommit:
    operation_id = f"operation.dependent.{suffix}"
    page_id = f"page.dependent.{suffix}"
    revision_id = f"{page_id}.rev1"
    body = f"Dependent claim {suffix}".encode()
    claim = Claim(
        claim_id=f"claim.dependent.{suffix}",
        kind=ClaimKind.FACT,
        statement=body.decode(),
        status=ClaimStatus.ACTIVE,
        evidence_refs=(evidence,),
        admission_reason="Derived from the exact memory entry revision.",
        observed_at=NOW,
    )
    revision = KnowledgeRevision(
        page_id=page_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        title=page_id,
        claims=(claim,),
        scope=actor.conversation_scope,
    )
    page = WikiPage(
        page_id=page_id,
        title=page_id,
        scope=actor.conversation_scope,
        current_revision_id=revision_id,
        status=WikiPageStatus.ACTIVE,
    )
    return CatalogCommit(
        operation_id=operation_id,
        actor=actor,
        payload_sha256=contract_sha256(revision),
        receipt=_receipt(operation_id, revision_id),
        page_writes=(
            PageRevisionWrite(
                page=page,
                revision=revision,
                expected=HeadExpectation(page_id, None, revision_id),
                prepared_file=repository.files.prepare(
                    RevisionFileDraft(
                        operation_id=operation_id,
                        target=KnowledgeRevisionTarget(page_id=page_id, revision_id=revision_id),
                        content=body,
                        sha256=revision.body_sha256,
                    )
                ),
            ),
        ),
    )


def _dependency_invalidation_commit(
    actor: ActorContext,
    evidence: EvidenceRef,
) -> CatalogCommit:
    operation_id = "operation.invalidate.memory-entry"
    return CatalogCommit(
        operation_id=operation_id,
        actor=actor,
        payload_sha256=contract_sha256(evidence),
        receipt=OperationReceipt(
            schema="knowledge.operation-receipt.v1",
            operation_id=operation_id,
            status=OperationStatus.APPLIED,
            resulting_revision_ids=(),
            retryable=False,
            occurred_at=NOW,
        ),
        dependency_invalidations=(
            EvidenceDependencyInvalidation(
                upstream_kind="memory_entry",
                upstream_id=evidence.evidence_id,
                upstream_revision_id=evidence.revision_id,
                resulting_state=DependencyState.STALE,
                reason="The canonical memory entry was superseded.",
            ),
        ),
    )


def _commit_upstream_memory(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
) -> EvidenceRef:
    write, entry = _memory_entry_write(repository, actor)
    operation_id = "operation.memory.upstream"
    _ = repository.commit_catalog(
        CatalogCommit(
            operation_id=operation_id,
            actor=actor,
            payload_sha256=contract_sha256(write.revision),
            receipt=_receipt(operation_id, write.revision.revision_id),
            memory_writes=(write,),
        )
    )
    return EvidenceRef(
        evidence_kind=EvidenceKind.MEMORY_ENTRY,
        evidence_id=entry.entry_id,
        revision_id=write.revision.revision_id,
        scope=actor.conversation_scope,
    )


def test_memory_entry_invalidation_fences_current_claim_without_mutating_history(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    upstream = _commit_upstream_memory(repository, actor)
    dependent = _dependent_page_commit(repository, actor, upstream, "existing")
    _ = repository.commit_catalog(dependent)
    write = dependent.page_writes[0]
    claim = write.revision.claims[0]
    target = KnowledgeRevisionTarget(write.page.page_id, write.revision.revision_id)
    historical_body = repository.files.read(
        repository.files.published(target, write.revision.body_sha256)
    )
    before = repository.read_page(actor, write.page.page_id, write.revision.revision_id)
    assert before is not None
    assert before.revision.claims == (claim,)
    assert KnowledgeRetriever(repository).search(
        actor,
        SearchRequest(query=claim.statement, corpus=SearchCorpus.WIKI),
        now=NOW,
    ).hits

    # When
    invalidation = _dependency_invalidation_commit(actor, upstream)
    receipt = repository.commit_catalog(invalidation)

    # Then
    assert receipt == invalidation.receipt
    assert repository.claim_dependency_state(
        actor,
        claim.claim_id,
        write.revision.revision_id,
    ) is DependencyState.STALE
    with pytest.raises(EvidenceResolutionError):
        _ = repository.resolve_evidence(
            actor,
            EvidenceRef(
                evidence_kind=EvidenceKind.CLAIM,
                evidence_id=claim.claim_id,
                revision_id=write.revision.revision_id,
                scope=actor.conversation_scope,
            ),
        )
    current = repository.read_page(actor, write.page.page_id)
    historical = repository.read_page(actor, write.page.page_id, write.revision.revision_id)
    assert current is not None
    assert historical is not None
    assert current.revision.claims == ()
    assert historical.revision.claims == (claim,)
    assert repository.files.read(
        repository.files.published(target, write.revision.body_sha256)
    ) == (historical_body)
    assert KnowledgeRetriever(repository).search(
        actor,
        SearchRequest(query=claim.statement, corpus=SearchCorpus.WIKI),
        now=NOW,
    ).hits == ()


def test_concurrent_dependent_commit_cannot_escape_memory_entry_invalidation(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "knowledge"
    repository = SqliteKnowledgeRepository(root)
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    upstream = _commit_upstream_memory(repository, actor)
    invalidation_has_write_lock = Event()
    release_invalidation = Event()
    dependent_file_published = Event()

    def hold_invalidation(boundary: RepositoryCommitBoundary) -> None:
        if boundary is RepositoryCommitBoundary.BEFORE_DB_COMMIT:
            invalidation_has_write_lock.set()
            assert release_invalidation.wait(timeout=10)

    def observe_dependent(boundary: RepositoryCommitBoundary) -> None:
        if boundary is RepositoryCommitBoundary.AFTER_FILE_PUBLISH:
            dependent_file_published.set()

    invalidation_repository = SqliteKnowledgeRepository(root, crash_injector=hold_invalidation)
    dependent_repository = SqliteKnowledgeRepository(root, crash_injector=observe_dependent)
    invalidation = _dependency_invalidation_commit(actor, upstream)
    dependent = _dependent_page_commit(dependent_repository, actor, upstream, "concurrent")
    completed: list[str] = []

    def invalidate() -> None:
        completed.append(invalidation_repository.commit_catalog(invalidation).operation_id)

    def create_dependent() -> None:
        completed.append(dependent_repository.commit_catalog(dependent).operation_id)

    # When
    invalidation_thread = Thread(target=invalidate)
    invalidation_thread.start()
    assert invalidation_has_write_lock.wait(timeout=10)
    dependent_thread = Thread(target=create_dependent)
    dependent_thread.start()
    assert dependent_file_published.wait(timeout=10)
    assert dependent_thread.is_alive()
    release_invalidation.set()
    invalidation_thread.join(timeout=10)
    dependent_thread.join(timeout=10)

    # Then
    assert not invalidation_thread.is_alive()
    assert not dependent_thread.is_alive()
    assert set(completed) == {invalidation.operation_id, dependent.operation_id}
    claim = dependent.page_writes[0].revision.claims[0]
    assert repository.claim_dependency_state(
        actor,
        claim.claim_id,
        dependent.page_writes[0].revision.revision_id,
    ) is DependencyState.STALE
    assert repository.read_page(actor, dependent.page_writes[0].page.page_id).revision.claims == ()


def _page_commit(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    suffix: str,
    body: bytes | None = None,
) -> CatalogCommit:
    operation_id = f"operation.crash.{suffix}"
    page_id = f"page.crash.{suffix}"
    revision_id = f"{page_id}.rev1"
    content = f"body {suffix}".encode() if body is None else body
    revision = KnowledgeRevision(
        page_id=page_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(content).hexdigest(),
        title=page_id,
        scope=_scope("workspace.a"),
    )
    page = WikiPage(
        page_id=page_id,
        title=page_id,
        scope=revision.scope,
        current_revision_id=revision_id,
        status=WikiPageStatus.ACTIVE,
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=KnowledgeRevisionTarget(page_id=page_id, revision_id=revision_id),
            content=content,
            sha256=revision.body_sha256,
        )
    )
    return CatalogCommit(
        operation_id=operation_id,
        actor=actor,
        payload_sha256=contract_sha256(revision),
        receipt=_receipt(operation_id, revision_id),
        page_writes=(
            PageRevisionWrite(
                page=page,
                revision=revision,
                expected=HeadExpectation(page_id, None, revision_id),
                prepared_file=prepared,
            ),
        ),
    )


def _actor_at_policy_epoch(epoch: int) -> ActorContext:
    actor = _actor()
    grants = tuple(grant.model_copy(update={"policy_epoch": epoch}) for grant in actor.grants)
    return actor.model_copy(update={"policy_epoch": epoch, "grants": grants})


def test_run_binding_write_read_and_replay_are_scoped_without_run_ledger(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    binding = RunBinding(
        binding_id="binding.run.a.1",
        run_id="run.a",
        binding_revision=1,
        workspace_id=actor.workspace_id,
        member_id=actor.member_id,
        session_id=actor.session_id,
        scope=actor.conversation_scope,
        grant_set_sha256=sha256(
            "".join(sorted(contract_sha256(grant) for grant in actor.grants)).encode()
        ).hexdigest(),
        policy_epoch=actor.policy_epoch,
        brand_id=None,
        action_kind=KnowledgeActionKind.RESEARCH,
        state=RunBindingState.ACTIVE,
    )

    # When
    first = repository.put_run_binding(actor, binding)
    replay = repository.put_run_binding(actor, binding)

    # Then
    assert first == replay == repository.run_binding(actor, binding.binding_id)
    with repository.connection() as connection:
        table_rows = _STRING_ROWS.validate_python(
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )
        tables = {row[0] for row in table_rows}
        count_row = _OPTIONAL_COUNT_ROW.validate_python(
            connection.execute("SELECT COUNT(*) FROM run_bindings").fetchone()
        )
    assert count_row is not None
    assert count_row[0] == 1
    assert {"agent_runs", "marketing_review_runs", "runs"}.isdisjoint(tables)

    conflicting = replace(binding, action_kind=KnowledgeActionKind.CONTENT_WRITE)
    with pytest.raises(RepositoryConflictError, match="run_binding_idempotency_conflict"):
        _ = repository.put_run_binding(actor, conflicting)

    forged_grants = replace(binding, grant_set_sha256="0" * 64)
    with pytest.raises(RepositoryConflictError, match="run_binding_grant_set_conflict"):
        _ = repository.put_run_binding(actor, forged_grants)

    forged = replace(binding, workspace_id="workspace.forged")
    with pytest.raises(RepositoryConflictError, match="run_binding_scope_conflict"):
        _ = repository.put_run_binding(actor, forged)

    other_session = actor.model_copy(
        update={
            "actor_id": "actor.other",
            "member_id": "member.other",
            "session_id": "session.other",
        }
    )
    repository.register_actor(other_session, MembershipRole.ADMIN)
    assert repository.run_binding(other_session, binding.binding_id) is None
    newer_actor = actor.model_copy(update={"policy_epoch": actor.policy_epoch + 1})
    assert repository.run_binding(newer_actor, binding.binding_id) is None


@pytest.mark.parametrize(
    ("boundary", "file_published", "db_committed"),
    [
        (RepositoryCommitBoundary.BEFORE_FILE_PUBLISH, False, False),
        (RepositoryCommitBoundary.AFTER_FILE_PUBLISH, True, False),
        (RepositoryCommitBoundary.BEFORE_DB_COMMIT, True, False),
        (RepositoryCommitBoundary.AFTER_DB_COMMIT, True, True),
    ],
)
def test_catalog_crash_boundaries_preserve_replay_orphan_and_receipt_invariants(
    tmp_path: Path,
    boundary: RepositoryCommitBoundary,
    file_published: bool,
    db_committed: bool,
) -> None:
    # Given
    root = tmp_path / boundary.value / "knowledge"

    expected_boundary = boundary

    def crash_at(boundary: RepositoryCommitBoundary) -> None:
        if boundary == expected_boundary:
            raise _InjectedCrashError(boundary.value)

    repository = SqliteKnowledgeRepository(root, crash_injector=crash_at)
    actor = _actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    command = _page_commit(repository, actor, boundary.value)
    write = command.page_writes[0]
    target = KnowledgeRevisionTarget(
        page_id=write.page.page_id,
        revision_id=write.revision.revision_id,
    )

    # When
    with pytest.raises(_InjectedCrashError, match=boundary.value):
        _ = repository.commit_catalog(command)

    # Then
    assert repository.files.published_exists(target) is file_published
    assert repository.operation_receipt(actor, command.operation_id) == (
        command.receipt if db_committed else None
    )
    assert repository.page_head(actor, write.page.page_id) == (
        write.revision.revision_id if db_committed else None
    )
    replay_repository = SqliteKnowledgeRepository(root)
    assert replay_repository.commit_catalog(command) == command.receipt
    assert replay_repository.page_head(actor, write.page.page_id) == write.revision.revision_id


def test_catalog_rejects_stale_actor_before_first_empty_page_commit(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor_at_policy_epoch(3)
    repository.register_actor(actor, MembershipRole.ADMIN)
    command = _page_commit(repository, actor, "stale-first", body=b"")
    with repository.connection() as connection:
        _ = connection.execute(
            "UPDATE workspaces SET policy_epoch=4 WHERE workspace_id=?",
            (actor.workspace_id,),
        )

    # When / Then
    with pytest.raises(PolicyEpochStaleError, match="stale_policy_epoch"):
        _ = repository.commit_catalog(command)


def test_catalog_rechecks_actor_epoch_before_idempotent_replay(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor_at_policy_epoch(3)
    repository.register_actor(actor, MembershipRole.ADMIN)
    command = _page_commit(repository, actor, "stale-replay", body=b"")
    first = repository.commit_catalog(command)
    with repository.connection() as connection:
        _ = connection.execute(
            "UPDATE workspaces SET policy_epoch=4 WHERE workspace_id=?",
            (actor.workspace_id,),
        )

    # When / Then
    with pytest.raises(PolicyEpochStaleError, match="stale_policy_epoch"):
        _ = repository.commit_catalog(command)
    assert repository.operation_receipt(actor, command.operation_id) == first


def test_soul_persistence_keeps_brand_heads_unique_scoped_replay_stable_and_view_derived(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor_a = _actor("workspace.a")
    actor_b = _actor("workspace.b")
    repository.register_actor(actor_a, MembershipRole.ADMIN)
    repository.register_actor(actor_b, MembershipRole.ADMIN)
    registration_a = _brand_registration(repository, actor_a, "brand.a", "a")
    registration_b = _brand_registration(repository, actor_a, "brand.b", "b")
    registration_cross = _brand_registration(repository, actor_b, "brand.cross", "cross")

    # When
    first_a = repository.register_brand(registration_a)
    replay_a = repository.register_brand(registration_a)
    _ = repository.register_brand(registration_b)
    _ = repository.register_brand(registration_cross)

    # Then
    stored_a = repository.read_memory(actor_a, registration_a.document.document_id)
    stored_b = repository.read_memory(actor_a, registration_b.document.document_id)
    assert stored_a is not None
    assert stored_b is not None
    assert stored_a.revision.revision_id != stored_b.revision.revision_id
    assert first_a == replay_a == registration_a.receipt
    with repository.connection() as connection:
        brand_count = _OPTIONAL_COUNT_ROW.validate_python(
            connection.execute(
                "SELECT COUNT(*) FROM brands WHERE workspace_id=? AND brand_id=?",
                (actor_a.workspace_id, registration_a.brand.brand_id),
            ).fetchone()
        )
        head_count = _OPTIONAL_COUNT_ROW.validate_python(
            connection.execute(
                "SELECT COUNT(*) FROM memory_heads WHERE workspace_id=? AND document_id=?",
                (actor_a.workspace_id, registration_a.document.document_id),
            ).fetchone()
        )
    assert brand_count is not None
    assert brand_count[0] == 1
    assert head_count is not None
    assert head_count[0] == 1

    duplicate_operation = "operation.soul.duplicate"
    duplicate = _memory_write(
        repository,
        actor_a,
        operation_id=duplicate_operation,
        document_id="memory.soul.a.duplicate",
        brand_id=registration_a.brand.brand_id,
    )
    with pytest.raises(RepositoryConflictError, match="catalog_integrity_conflict"):
        _ = repository.commit_catalog(
            CatalogCommit(
                operation_id=duplicate_operation,
                actor=actor_a,
                payload_sha256=contract_sha256(duplicate.document),
                receipt=_receipt(duplicate_operation, duplicate.revision.revision_id),
                memory_writes=(duplicate,),
            )
        )

    cross_operation = "operation.soul.cross"
    cross_workspace = _memory_write(
        repository,
        actor_a,
        operation_id=cross_operation,
        document_id="memory.soul.cross-workspace",
        brand_id=registration_cross.brand.brand_id,
    )
    with pytest.raises(RepositoryConflictError, match="catalog_integrity_conflict"):
        _ = repository.commit_catalog(
            CatalogCommit(
                operation_id=cross_operation,
                actor=actor_a,
                payload_sha256=contract_sha256(cross_workspace.document),
                receipt=_receipt(cross_operation, cross_workspace.revision.revision_id),
                memory_writes=(cross_workspace,),
            )
        )

    current_view = (
        repository.root
        / "teams"
        / actor_a.workspace_id
        / "brands"
        / registration_a.brand.brand_id
        / "SOUL.md"
    )
    current_view.parent.mkdir(mode=0o700, parents=True)
    _ = current_view.write_bytes(b"drifted current view")
    current_view.chmod(0o600)
    authoritative = repository.read_memory(actor_a, registration_a.document.document_id)
    assert authoritative is not None
    assert authoritative.body == f"immutable {registration_a.brand.brand_id}".encode()
    assert authoritative.body != current_view.read_bytes()
