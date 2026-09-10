from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING

from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.contract_types import GrantCapability, MemoryStatus
from ads_booster.knowledge.contracts import AccessScope, MemoryKind, ScopeKind
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_context import known_memory
from ads_booster.knowledge.curation_contracts import CurationMemoryIntent, CurationRunStatus
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.curation_memory_payload import memory_payload
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.maintenance_jobs import ProcessCancellation
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.memory_consolidation_views import MemoryViewDispatcher
from ads_booster.knowledge.operation_enums import JobPriority, JobState
from ads_booster.knowledge.repository import JobClaim, MembershipRole
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import actor
from tests.knowledge.test_curation_inputs import curation_input as curation_input  # noqa: PLC0414
from tests.knowledge.test_curation_inputs import envelope
from tests.knowledge.test_curation_remember import MemoryProvider

if TYPE_CHECKING:
    from ads_booster.knowledge.batch_curation import CurationBatchWork
    from ads_booster.knowledge.jobs import JobProcessResult
    from tests.knowledge.test_curation_inputs import CurationInput


def _channel_work(fixture: CurationInput, channel: str, thread: str) -> CurationBatchWork:
    repository, processor, _, original, _ = fixture
    scope = AccessScope(
        kind=ScopeKind.CHANNEL, workspace_id=processor.actor.workspace_id, channel_id=channel
    )
    scoped = actor(scope=scope).model_copy(
        update={
            "actor_id": f"actor.{channel}",
            "member_id": f"member.{channel}",
            "session_id": f"session.{channel}",
            "grants": tuple(
                grant.model_copy(update={"grant_id": f"{channel}.{grant.grant_id}"})
                for grant in actor(scope=scope).grants
            ),
        }
    )
    repository.register_actor(scoped, MembershipRole.ADMIN)
    event = original.model_copy(
        update={
            "scope": scope,
            "speaker_ref": scoped.actor_id,
            "message_id": f"message.{channel}.{thread}",
            "conversation_id": thread,
            "text": f"Project {channel} budget is 41000",
        }
    )
    ingested = KnowledgeIngestion(repository).ingest(
        scoped, event, envelope(event, f"delivery.{channel}.{thread}")
    )
    now = datetime.now(UTC)
    while True:
        lease = repository.claim_job(JobClaim("worker.channel", now, now + timedelta(minutes=1)))
        assert lease is not None
        if lease.job.job_id == ingested.unit_receipts[0].receipt.curation_job_id:
            return processor.build_curation_work(lease.job, scoped)


def _remember(fixture: CurationInput, work: CurationBatchWork) -> str:
    repository = fixture[0]
    current = work.request.authenticated_user_event
    assert current is not None
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        CurationMemoryIntent(
            subject_key="Project",
            text=work.request.objective,
            evidence_ids=(current.evidence_ref.evidence_id,),
        ),
        work.trusted_context,
    )
    assert result.status is ToolResultStatus.APPLIED, result
    document_id = repository.find_memory_document_id(
        work.trusted_context.actor, MemoryKind.CORE, None, None
    )
    assert document_id is not None
    return document_id


def test_channel_memory_is_shared_across_threads_and_excludes_other_channels(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    _ = _remember(curation_input, processor.build_curation_work(job))
    first = _channel_work(curation_input, "C1", "thread.first")
    first_id = _remember(curation_input, first)
    other = _channel_work(curation_input, "C2", "thread.first")
    other_id = _remember(curation_input, other)
    # When
    following = _channel_work(curation_input, "C1", "thread.next")
    # Then
    assert first_id != other_id
    assert len(following.request.known_memory) == 1
    assert "C1" in following.request.known_memory[0].text
    assert following.request.known_memory[0].scope == first.trusted_context.actor.conversation_scope
    assert len(following.request.conversation_evidence) == 1
    assert "C2" not in following.request.conversation_evidence[0].text
    stored = repository.read_memory(first.trusted_context.actor, first_id)
    assert stored is not None
    assert stored.document.owned_scope == first.trusted_context.actor.conversation_scope
    assert known_memory(repository, other.trusted_context.actor, ())[0].document_id == other_id


def test_channel_consolidation_and_views_preserve_independent_scope(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    first = _channel_work(curation_input, "C1", "thread.first")
    first_id = _remember(curation_input, first)
    second = _channel_work(curation_input, "C2", "thread.second")
    second_id = _remember(curation_input, second)
    now = datetime.now(UTC)
    processors = tuple(
        MemoryConsolidationProcessor(
            repository, work.trusted_context.actor, ChangePublisher(repository)
        )
        for work in (first, second)
    )
    jobs = tuple(processor.schedule(root_event_id="root", due_at=now) for processor in processors)
    assert all(job is not None for job in jobs)
    # When
    root_processor = replace(
        curation_input[1],
        memory=MemoryConsolidationProcessor(
            repository,
            curation_input[1].actor,
            ChangePublisher(repository),
        ),
    )
    results: list[JobProcessResult] = []
    for _ in range(6):
        lease = repository.claim_job(JobClaim("worker.memory", now, now + timedelta(minutes=1)))
        assert lease is not None
        assert lease.job.submitter_actor is not None
        assert lease.job.submitter_actor.conversation_scope == lease.job.scope
        results.append(root_processor.process(lease, Event()))
    # Then
    assert all(result.state is JobState.COMPLETED for result in results)
    views = tuple(
        repository.files.root
        / "teams"
        / work.trusted_context.actor.workspace_id
        / "channels"
        / scope_key(work.trusted_context.actor.conversation_scope)
        / "MEMORY.md"
        for work in (first, second)
    )
    assert "C1" in views[0].read_text()
    assert "C2" in views[1].read_text()
    assert views[0] != views[1]
    assert not (
        repository.files.root / "teams" / first.trusted_context.actor.workspace_id / "MEMORY.md"
    ).exists()
    assert not MemoryViewDispatcher(repository, first.trusted_context.actor).dispatch_once()
    assert first_id != second_id


def test_channel_curation_enables_memory_and_schedules_same_channel(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    work = _channel_work(curation_input, "C1", "thread.first")
    host = ToolHost(repository)
    runner = CurationRunner(
        CurationDependencies(
            provider=MemoryProvider(),
            tool_host=host,
            dispositions=RepositorySourceDisposition(repository),
            memory=CurationMemoryWriter(repository, host),
        )
    )
    processor = replace(
        curation_input[1],
        curation=runner,
        memory=MemoryConsolidationProcessor(
            repository,
            curation_input[1].actor,
            ChangePublisher(repository),
        ),
    )
    # When
    result = processor.run_curation_work(work, ProcessCancellation(Event()), JobPriority.ROUTINE)
    # Then
    assert result.status is CurationRunStatus.FINISHED
    now = datetime.now(UTC)
    lease = repository.claim_job(JobClaim("worker.verify", now, now + timedelta(minutes=1)))
    assert lease is not None
    assert lease.job.scope == work.trusted_context.actor.conversation_scope
    assert lease.job.submitter_actor == work.trusted_context.actor


def test_view_dispatcher_cannot_claim_or_inspect_other_channel(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    first = _channel_work(curation_input, "C1", "thread.first")
    _ = _remember(curation_input, first)
    second = _channel_work(curation_input, "C2", "thread.second")
    second_id = _remember(curation_input, second)
    stored = repository.read_memory(second.trusted_context.actor, second_id)
    assert stored is not None
    dispatcher = MemoryViewDispatcher(repository, first.trusted_context.actor)
    # When
    denied = dispatcher.dispatch_target(second_id, stored.revision.revision_id)
    # Then
    assert not denied.processed
    assert not denied.completed
    assert denied.code == "memory_view_outbox_missing"
    assert dispatcher.dispatch_once()
    assert not dispatcher.dispatch_once()
    assert MemoryViewDispatcher(repository, second.trusted_context.actor).dispatch_once()


def test_explicit_channel_schedule_preserves_submitter(curation_input: CurationInput) -> None:
    # Given
    repository = curation_input[0]
    work = _channel_work(curation_input, "C1", "thread.first")
    document_id = _remember(curation_input, work)
    scoped = work.trusted_context.actor
    scoped = scoped.model_copy(
        update={
            "grants": (
                *scoped.grants,
                scoped.grants[0].model_copy(
                    update={"grant_id": "grant.C1.schedule", "capability": GrantCapability.SCHEDULE}
                ),
            )
        }
    )
    repository.register_actor(scoped, MembershipRole.ADMIN)
    now = datetime.now(UTC)
    # When
    result = ToolHost(repository).execute(
        "knowledge_schedule",
        {
            "schema": "knowledge.tool.schedule.v1",
            "operation_id": "schedule.C1",
            "kind": "memory_view_refresh",
            "targets": [document_id],
            "due_at": now.isoformat(),
            "purpose": "Refresh current channel view",
            "triggers": [work.request.event_id],
        },
        work.trusted_context.model_copy(update={"actor": scoped}),
    )
    # Then
    assert result.status is ToolResultStatus.APPLIED
    lease = repository.claim_job(JobClaim("worker.scheduled", now, now + timedelta(minutes=1)))
    assert lease is not None
    assert lease.job.submitter_actor == scoped
    assert lease.job.scope == scoped.conversation_scope


def test_consolidation_publishes_only_its_channel_document(curation_input: CurationInput) -> None:
    # Given
    repository = curation_input[0]
    first = _channel_work(curation_input, "C1", "thread.first")
    first_id = _remember(curation_input, first)
    second = _channel_work(curation_input, "C2", "thread.second")
    second_id = _remember(curation_input, second)
    original = repository.read_memory(first.trusted_context.actor, first_id)
    other = repository.read_memory(second.trusted_context.actor, second_id)
    assert original is not None
    assert other is not None
    payload = memory_payload(
        repository,
        original,
        original.entries[0].model_copy(
            update={
                "entry_id": "duplicate.C1",
            }
        ),
        "operation.duplicate",
        first.trusted_context,
    )
    assert (
        ToolHost(repository)
        .execute(
            "memory_apply",
            payload.model_dump(mode="json", by_alias=True),
            first.trusted_context,
        )
        .status
        is ToolResultStatus.APPLIED
    )
    processor = MemoryConsolidationProcessor(
        repository, first.trusted_context.actor, ChangePublisher(repository)
    )
    now = datetime.now(UTC)
    assert processor.schedule(root_event_id="root", due_at=now) is not None
    lease = repository.claim_job(JobClaim("worker.consolidate", now, now + timedelta(minutes=1)))
    assert lease is not None
    # When
    result = processor.process(lease, Event())
    # Then
    assert result.state is JobState.COMPLETED
    changed = repository.read_memory(first.trusted_context.actor, first_id)
    assert changed is not None
    assert [entry.status for entry in changed.entries] == [
        MemoryStatus.ACTIVE,
        MemoryStatus.SUPERSEDED,
    ]
    assert changed.document.owned_scope == first.trusted_context.actor.conversation_scope
    assert repository.read_memory(second.trusted_context.actor, second_id) == other
