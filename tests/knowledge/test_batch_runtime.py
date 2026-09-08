from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import BatchCurationCoordinator, CurationBatchItem
from ads_booster.knowledge.contracts import KnowledgeJob
from ads_booster.knowledge.operation_enums import BatchState, JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import (
    JobRegistration,
    MembershipRole,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.runtime import SqliteBatchFlusher
from tests.knowledge.batch_runtime_support import FixtureBatchRuntime, batch_fixture
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.change_test_fixtures import actor as catalog_actor

if TYPE_CHECKING:
    from pathlib import Path

_TEXT_PAIRS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


def test_flush_keeps_catalog_state_consistent_for_claim(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = catalog_actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id="job.flush",
        workspace_id=actor.workspace_id,
        scope=actor.conversation_scope,
        kind=JobKind.CURATION,
        state=JobState.QUEUED,
        priority=JobPriority.ROUTINE,
        root_event_id="event.flush",
        policy_version="policy.v1",
        due_at=NOW,
        created_at=NOW,
    )
    repository.put_job(JobRegistration(job=job, unique_key=job.job_id))
    coordinator = BatchCurationCoordinator(repository)
    item = CurationBatchItem(
        job.job_id,
        job.root_event_id,
        1,
        actor,
        job.policy_version,
        job.priority,
        NOW,
    )
    collected = coordinator.collect(item)
    assert coordinator.collect(item) == collected
    assert coordinator.claim(actor, NOW + timedelta(seconds=59)) is None

    # When
    count = SqliteBatchFlusher(repository).flush_ready_batches(actor.workspace_id, NOW)
    running = coordinator.claim(actor, NOW)

    # Then
    assert count == 1
    assert running is not None
    assert running.state is BatchState.RUNNING
    assert running.batch_deadline == collected.batch_deadline
    assert running.event_receipts == collected.event_receipts
    assert SqliteBatchFlusher(repository).flush_ready_batches(actor.workspace_id, NOW) == 0


def test_routine_window_stays_at_first_event_and_persists_each_receipt(tmp_path: Path) -> None:
    # Given
    fixture = batch_fixture(tmp_path / "knowledge")
    fixture.provider.release.set()
    try:
        fixture.put("first")
        assert fixture.runtime.tick(now=NOW)
        fixture.put("second", at=NOW + timedelta(seconds=30))
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=30))
        assert not fixture.runtime.tick(now=NOW + timedelta(seconds=59))
        assert not fixture.runtime.active

        # When
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))

        # Then
        assert fixture.provider.calls.get(timeout=1) == ("job.first", "job.second")
        assert fixture.states() == (("job.first", "completed"), ("job.second", "completed"))
        with fixture.repository.connection() as connection:
            receipts = connection.execute(
                "SELECT event_id,result_status FROM batch_items ORDER BY event_id"
            ).fetchall()
        assert _TEXT_PAIRS.validate_python(receipts) == [
            ("event.first", "rejected"),
            ("event.second", "rejected"),
        ]
    finally:
        fixture.close()


def test_urgent_cancels_routine_then_restarts_unfinished_events(tmp_path: Path) -> None:
    # Given
    fixture = batch_fixture(tmp_path / "knowledge")
    at = NOW + timedelta(seconds=60)
    try:
        fixture.put("routine")
        assert fixture.runtime.tick(now=at)
        assert fixture.provider.started.wait(timeout=5)
        assert fixture.provider.calls.get(timeout=1) == ("job.routine",)

        # When
        fixture.put("urgent", priority=JobPriority.URGENT, at=at)
        assert fixture.runtime.tick(now=at)
        fixture.provider.release.set()
        fixture.runtime.reap(at)
        assert fixture.runtime.tick(now=at)
        fixture.runtime.reap(at)
        assert fixture.runtime.tick(now=at)
        fixture.runtime.reap(at)

        # Then
        assert fixture.provider.calls.get(timeout=1) == ("job.urgent",)
        assert fixture.provider.calls.get(timeout=1) == ("job.routine",)
        assert fixture.states() == (("job.routine", "completed"), ("job.urgent", "completed"))
        with fixture.repository.connection() as connection:
            batches = _TEXT_PAIRS.validate_python(
                connection.execute(
                    "SELECT batch_id,state FROM curation_batches ORDER BY state"
                ).fetchall()
            )
        assert [state for _, state in batches] == ["cancelled", "completed", "completed"]
    finally:
        fixture.close()


def test_shutdown_releases_running_events_for_next_runtime(tmp_path: Path) -> None:
    # Given
    fixture = batch_fixture(tmp_path / "knowledge")
    at = NOW + timedelta(seconds=60)
    try:
        fixture.put("shutdown")
        assert fixture.runtime.tick(now=at)
        assert fixture.provider.started.wait(timeout=5)

        # When
        fixture.runtime.cancel()
        fixture.provider.release.set()
        fixture.runtime.shutdown(wait=True)

        # Then
        assert not fixture.runtime.active
        assert fixture.states() == (("job.shutdown", "queued"),)
        fixture.runtime.close_queue()
        fixture = replace(
            fixture,
            runtime=FixtureBatchRuntime(fixture.repository, fixture.actor, fixture.runtime.jobs),
        )
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=at)
        fixture.runtime.reap(at)
        assert fixture.states() == (("job.shutdown", "completed"),)
    finally:
        fixture.close()
