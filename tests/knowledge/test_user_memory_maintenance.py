from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.contract_types import GrantCapability, MemoryKind, ScopeKind
from ads_booster.knowledge.curation_contracts import CurationMemoryDestination, CurationMemoryIntent
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.memory_consolidation_views import (
    MemoryViewDispatcher,
    memory_maintenance_scopes,
)
from ads_booster.knowledge.memory_consolidation_views import (
    memory_view_path as materialized_view_path,
)
from ads_booster.knowledge.memory_contracts import MemoryDocument
from ads_booster.knowledge.operation_enums import JobKind, JobState
from ads_booster.knowledge.repository import JobClaim, MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.scope_contracts import AccessScope, channel_member_scope
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import actor
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.batch_curation import CurationBatchWork
    from ads_booster.knowledge.operation_contracts import KnowledgeJob
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def _personal_work(fixture: CurationInput, member: str) -> CurationBatchWork:
    repository, processor, _, original, _ = fixture
    channel = AccessScope(
        kind=ScopeKind.CHANNEL,
        workspace_id=processor.actor.workspace_id,
        channel_id="C-personal",
    )
    principal = actor(scope=channel).model_copy(
        update={
            "actor_id": f"actor.{member}",
            "member_id": member,
            "session_id": f"session.{member}",
            "grants": tuple(
                grant.model_copy(
                    update={
                        "grant_id": f"{member}.{grant.grant_id}",
                    }
                )
                for grant in actor(scope=channel).grants
            ),
        }
    )
    personal_scope = channel_member_scope(principal)
    assert personal_scope is not None
    principal = principal.model_copy(
        update={
            "grants": (
                *principal.grants,
                *(
                    grant.model_copy(
                        update={
                            "grant_id": f"personal.{grant.grant_id}",
                            "scope": personal_scope,
                        }
                    )
                    for grant in principal.grants
                ),
            ),
        }
    )
    repository.register_actor(principal, MembershipRole.EDITOR)
    event = original.model_copy(
        update={
            "scope": channel,
            "speaker_ref": principal.actor_id,
            "message_id": f"message.{member}",
            "conversation_id": f"thread.{member}",
            "text": f"Preference for {member}",
        }
    )
    ingested = KnowledgeIngestion(repository).ingest(
        principal,
        event,
        envelope(event, f"delivery.{member}"),
    )
    now = datetime.now(UTC)
    while True:
        lease = repository.claim_job(JobClaim("worker.fixture", now, now + timedelta(minutes=1)))
        assert lease is not None
        if lease.job.job_id == ingested.unit_receipts[0].receipt.curation_job_id:
            return processor.build_curation_work(lease.job, principal)


def _write_user(fixture: CurationInput, work: CurationBatchWork) -> str:
    evidence = work.request.authenticated_user_event
    assert evidence is not None
    result = CurationMemoryWriter(fixture[0], ToolHost(fixture[0])).write(
        work.request,
        CurationMemoryIntent(
            destination=CurationMemoryDestination.USER,
            subject_key="personal preference",
            text=f"Preference for {work.trusted_context.actor.member_id}",
            evidence_ids=(evidence.evidence_ref.evidence_id,),
        ),
        work.trusted_context,
    )
    assert result.status is ToolResultStatus.APPLIED, result
    document_id = fixture[0].find_memory_document_id(
        work.trusted_context.actor,
        MemoryKind.USER,
        None,
        None,
    )
    assert document_id is not None
    return document_id


def _view_path(repository: SqliteKnowledgeRepository, work: CurationBatchWork) -> Path:
    principal = work.trusted_context.actor
    return (
        repository.files.root
        / "teams"
        / principal.workspace_id
        / "channels"
        / scope_key(principal.conversation_scope)
        / "users"
        / quote(principal.member_id, safe="._-")
        / "USER.md"
    )


def test_user_consolidation_preserves_owner_through_restart_and_materializes_separate_views(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    first = _personal_work(curation_input, "U-first")
    first_id = _write_user(curation_input, first)
    second = _personal_work(curation_input, "U-second")
    second_id = _write_user(curation_input, second)
    now = datetime.now(UTC)
    for work in (first, second):
        job = MemoryConsolidationProcessor(
            repository,
            work.trusted_context.actor,
            ChangePublisher(repository),
        ).schedule(root_event_id=work.request.event_id, due_at=now)
        assert job is not None
    reopened = SqliteKnowledgeRepository(repository.root)
    root = replace(
        curation_input[1],
        repository=reopened,
        memory=MemoryConsolidationProcessor(
            reopened,
            curation_input[1].actor,
            ChangePublisher(reopened),
        ),
    )
    # When
    processed: list[KnowledgeJob] = []
    for _ in range(6):
        lease = reopened.claim_job(JobClaim("worker.user", now, now + timedelta(minutes=1)))
        assert lease is not None
        result = root.process(lease, Event())
        assert result.state is JobState.COMPLETED, result.payload
        processed.append(lease.job)
    # Then
    assert first_id != second_id
    assert "U-first" in _view_path(repository, first).read_text()
    assert "U-second" not in _view_path(repository, first).read_text()
    assert "U-second" in _view_path(repository, second).read_text()
    descendants = [job for job in processed if job.kind is not JobKind.MEMORY_CONSOLIDATE]
    assert len(descendants) == 4
    assert all(job.scope.kind is ScopeKind.CHANNEL_MEMBER for job in descendants)
    assert all(
        job.submitter_actor is not None and job.submitter_actor.member_id == job.scope.member_id
        for job in descendants
    )
    assert not MemoryViewDispatcher(reopened, first.trusted_context.actor).dispatch_once()


def test_user_view_cannot_claim_another_members_outbox(curation_input: CurationInput) -> None:
    # Given
    repository = curation_input[0]
    first = _personal_work(curation_input, "U-first")
    _ = _write_user(curation_input, first)
    second = _personal_work(curation_input, "U-second")
    second_id = _write_user(curation_input, second)
    second_memory = repository.read_memory(second.trusted_context.actor, second_id)
    assert second_memory is not None
    # When
    result = MemoryViewDispatcher(repository, first.trusted_context.actor).dispatch_target(
        second_id,
        second_memory.revision.revision_id,
    )
    # Then
    assert not result.processed
    assert not result.completed
    assert not _view_path(repository, second).exists()


def test_restarted_user_maintenance_rejects_revoked_personal_grant(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    work = _personal_work(curation_input, "U-revoked")
    _ = _write_user(curation_input, work)
    now = datetime.now(UTC)
    job = MemoryConsolidationProcessor(
        repository,
        work.trusted_context.actor,
        ChangePublisher(repository),
    ).schedule(root_event_id=work.request.event_id, due_at=now)
    assert job is not None
    personal = channel_member_scope(work.trusted_context.actor)
    assert personal is not None
    with repository.connection() as connection:
        _ = connection.execute(
            "DELETE FROM scope_grants WHERE member_id=? AND scope_key=? AND capability='write'",
            (work.trusted_context.actor.member_id, scope_key(personal)),
        )
    reopened = SqliteKnowledgeRepository(repository.root)
    root = replace(
        curation_input[1],
        repository=reopened,
        memory=MemoryConsolidationProcessor(
            reopened,
            curation_input[1].actor,
            ChangePublisher(reopened),
        ),
    )
    lease = reopened.claim_job(JobClaim("worker.revoked", now, now + timedelta(minutes=1)))
    assert lease is not None
    # When / Then
    with pytest.raises(KnowledgePolicyError):
        _ = root.process(lease, Event())
    assert not _view_path(repository, work).exists()
    assert not MemoryViewDispatcher(reopened, work.trusted_context.actor).dispatch_once()
    assert not _view_path(repository, work).exists()


def test_user_view_path_escapes_member_identity(tmp_path: Path) -> None:
    # Given
    channel = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="workspace", channel_id="C1")
    personal = AccessScope(
        kind=ScopeKind.CHANNEL_MEMBER,
        workspace_id="workspace",
        channel_id="C1",
        member_id="U:another.user",
    )
    document = MemoryDocument(
        document_id="personal",
        workspace_id="workspace",
        kind=MemoryKind.USER,
        timezone="UTC",
        head_revision_id="r1",
        scope=personal,
    )
    # When
    path = materialized_view_path(tmp_path, document)
    # Then
    assert path.relative_to(tmp_path).as_posix() == (
        f"teams/workspace/channels/{scope_key(channel)}/users/U%3Aanother.user/USER.md"
    )


def test_maintenance_scope_excludes_ungranted_personal_and_other_member() -> None:
    # Given
    channel = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="workspace.alpha", channel_id="C1")
    principal = actor(scope=channel)
    personal = channel_member_scope(principal)
    assert personal is not None
    other = personal.model_copy(update={"member_id": "other"})
    principal = principal.model_copy(
        update={
            "grants": (
                *principal.grants,
                *(grant.model_copy(update={"scope": other}) for grant in principal.grants),
                principal.grants[0].model_copy(
                    update={"scope": personal, "capability": GrantCapability.READ}
                ),
            )
        }
    )
    # When
    scopes = memory_maintenance_scopes(principal)
    # Then
    assert scopes == (channel,)
