from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.agent.service.drive_work import (
    DriveAdmissionConflict,
    DriveClaim,
    DriveClaimLostError,
    DriveOrigin,
    DriveWorkQueue,
)
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_progress import TaskProjection, seed_task, task_records
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.channels.http.jobs import AgentJobs, WebJob
from ads_booster.contracts.agent_run import AgentRunState

from .test_application import NOW as APP_NOW
from .test_application import build_service, run_request
from .test_task_drive import FreshResearch, Steps
from .test_task_progress import NOW, make_run, step

if TYPE_CHECKING:
    from pathlib import Path
    from sqlite3 import Connection

    from ads_booster.agent.service.application import CreateAgentRunRequest
    from ads_booster.contracts.agent_run import AgentRun, AgentStep


def _next_step(run: AgentRun) -> AgentStep:
    return step().model_copy(
        update={
            "step_id": f"task-step:{run.revision}",
            "sequence": run.revision,
            "parent_step_sha256": run.head_step_sha256,
            "occurred_at": max(NOW, run.updated_at),
        }
    )


def _queue_row(queue: DriveWorkQueue) -> tuple[int, str, str | None, str | None] | None:
    with queue.connect() as db:
        return cast(
            "tuple[int, str, str | None, str | None] | None",
            db.execute(
                """SELECT revision,state,claim_owner,lease_expires_at FROM agent_drive_work
                ORDER BY tenant_id,run_id LIMIT 1"""
            ).fetchone(),
        )


def _http_origin(run: AgentRun) -> DriveOrigin:
    return DriveOrigin(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        channel="http",
        principal_id="member",
        event_id="event",
    )


def _bound_task(
    repository: SqliteAgentRunRepository, queue: DriveWorkQueue
) -> tuple[AgentRun, TaskProjection, DriveOrigin]:
    run = repository.create(make_run())
    origin = _http_origin(run)
    queue.bind(origin)
    return run, seed_task(run), origin


def _admit_running(
    repository: SqliteAgentRunRepository, queue: DriveWorkQueue
) -> tuple[AgentRun, TaskProjection, DriveOrigin]:
    run, task, origin = _bound_task(repository, queue)
    admitted = repository.append_step(
        run,
        step(),
        state=AgentRunState.RUNNING,
        expected_revision=run.revision,
        records=task_records(run, task, NOW),
        admission=queue.transition(run, task, NOW),
    )
    return admitted, task, origin


def _terminal_task(task: TaskProjection) -> TaskProjection:
    return TaskProjection(
        task.spec,
        task.checkpoint.model_copy(update={"disposition": "satisfied", "next_action": "done"}),
    )


def _enqueue_bounded_http_create(jobs: AgentJobs, request: CreateAgentRunRequest) -> None:
    _ = jobs.enqueue(
        "trace",
        "member",
        WebJob(
            job_id="web",
            run_id=request.run_id,
            action="create",
            goal=request.goal,
            budget=request.budget.model_copy(update={"max_tool_calls": 12}),
        ),
        now=APP_NOW,
    )


def test_ownership_scope_keeps_multiple_transitions_claimed_until_slice_exit(
    tmp_path: Path,
) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "slice.sqlite3")
    owner = DriveWorkQueue(repository.database_path, owner_id="worker-a")
    contender = DriveWorkQueue(repository.database_path, owner_id="worker-b")
    run, task, origin = _bound_task(repository, owner)

    with owner.ownership(origin=origin, now=NOW):
        first = repository.append_step(
            run,
            step(),
            state=AgentRunState.RUNNING,
            expected_revision=run.revision,
            records=task_records(run, task, NOW),
            admission=owner.transition(run, task, NOW),
        )
        first_queue = _queue_row(owner)
        assert first_queue is not None
        assert first_queue[:3] == (first.revision, "running", "worker-a")
        original_claim = DriveClaim(
            origin,
            run.revision,
            "drive",
            "worker-a",
            NOW + timedelta(minutes=30),
        )
        contender.recover("http", now=NOW + timedelta(minutes=1))
        assert contender.claim("http", NOW + timedelta(minutes=1)) is None
        second = repository.append_step(
            first,
            _next_step(first),
            state=AgentRunState.RUNNING,
            expected_revision=first.revision,
            records=task_records(first, task, NOW),
            admission=owner.transition(first, task, NOW),
        )
        second_queue = _queue_row(owner)
        assert second_queue is not None
        assert second_queue[:3] == (second.revision, "running", "worker-a")
        renewed = owner.renew(original_claim, NOW + timedelta(minutes=2))
        assert renewed is not None
        assert renewed.revision == second.revision

    assert _queue_row(owner) == (second.revision, "pending", None, None)


def test_recovered_pending_claim_rejects_stale_in_slice_transition(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "stale.sqlite3")
    owner = DriveWorkQueue(
        repository.database_path,
        owner_id="worker-a",
        lease_duration=timedelta(minutes=5),
    )
    contender = DriveWorkQueue(repository.database_path, owner_id="worker-b")
    admitted, task, _ = _admit_running(repository, owner)
    claim = owner.claim("http", NOW)
    assert claim is not None

    with owner.ownership(claim=claim, now=NOW):
        contender.recover("http", now=NOW + timedelta(minutes=6))
        assert _queue_row(owner) == (admitted.revision, "pending", None, None)
        with owner.connect() as db, pytest.raises(DriveClaimLostError, match="claim_lost"):
            owner.transition(admitted, task, NOW + timedelta(minutes=6))(db)


def test_terminal_transition_releases_owned_slice_to_notification(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "terminal.sqlite3")
    owner = DriveWorkQueue(repository.database_path, owner_id="worker-a")
    notifier = DriveWorkQueue(repository.database_path, owner_id="worker-b")
    run, task, origin = _bound_task(repository, owner)
    with owner.connect() as db:
        _ = db.execute("""CREATE TABLE agent_web_jobs (
            tenant TEXT NOT NULL, job_id TEXT NOT NULL, request_json TEXT NOT NULL,
            state TEXT NOT NULL)""")
    terminal = _terminal_task(task)

    with owner.ownership(origin=origin, now=NOW):
        completed = repository.append_step(
            run,
            step(),
            state=AgentRunState.COMPLETED,
            expected_revision=run.revision,
            records=task_records(run, terminal, NOW),
            admission=owner.transition(run, terminal, NOW),
        )
        terminal_queue = _queue_row(owner)
        assert terminal_queue is not None
        assert terminal_queue[:3] == (completed.revision, "running", "worker-a")
        assert notifier.claim("http", NOW) is None
    assert _queue_row(owner) == (completed.revision, "notify", None, None)
    notification = notifier.claim("http", NOW)
    assert notification is not None
    assert notification.phase == "notify"


def test_queue_wake_is_atomic_with_checkpoint_and_single_claim(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "work.sqlite3")
    queue = DriveWorkQueue(
        repository.database_path,
        owner_id="worker-a",
        lease_duration=timedelta(minutes=5),
    )
    _, _, origin = _admit_running(repository, queue)
    claimed = queue.claim("http", NOW)
    assert claimed is not None
    assert claimed.origin == origin
    assert queue.claim("http", NOW) is None
    reopened = DriveWorkQueue(
        repository.database_path,
        owner_id="worker-b",
        lease_duration=timedelta(minutes=5),
    )
    reopened.recover("http", now=NOW + timedelta(minutes=6))
    reclaimed = reopened.claim("http", NOW + timedelta(minutes=6))
    assert reclaimed is not None
    assert reclaimed.origin == claimed.origin
    assert reclaimed.revision == claimed.revision
    assert reclaimed.owner_id != claimed.owner_id


def test_live_claim_is_not_recovered_by_overlapping_process(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "overlap.sqlite3")
    owner = DriveWorkQueue(
        repository.database_path,
        owner_id="worker-a",
        lease_duration=timedelta(minutes=5),
    )
    contender = DriveWorkQueue(
        repository.database_path,
        owner_id="worker-b",
        lease_duration=timedelta(minutes=5),
    )
    admitted, task, _ = _admit_running(repository, owner)

    first = owner.claim("http", NOW)
    assert first is not None
    contender.recover("http", now=NOW + timedelta(minutes=4))
    assert contender.claim("http", NOW + timedelta(minutes=4)) is None

    second = contender.claim("http", NOW + timedelta(minutes=6))
    assert second is not None
    assert second.owner_id == "worker-b"
    current = repository.get(admitted.tenant_id, admitted.run_id)
    assert current is not None
    with owner.connect() as db, pytest.raises(DriveClaimLostError, match="claim_lost"):
        owner.transition(current, task, NOW + timedelta(minutes=6))(db)
    assert owner.discard(first, now=NOW + timedelta(minutes=10)) is False
    assert contender.renew(second, NOW + timedelta(minutes=10)) is not None
    assert owner.renew(first, NOW + timedelta(minutes=10)) is None
    assert contender.discard(second, now=NOW + timedelta(minutes=10)) is True


def test_legacy_queue_schema_is_migrated_additively(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        _ = db.executescript("""
            CREATE TABLE agent_drive_origins (
                tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, origin_json TEXT NOT NULL,
                PRIMARY KEY(tenant_id,run_id));
            CREATE TABLE agent_drive_work (
                tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
                due_at TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(tenant_id,run_id));
        """)
        _ = db.execute(
            "INSERT INTO agent_drive_work VALUES(?,?,?,?,?)",
            ("tenant", "run", 7, NOW.isoformat(), "pending"),
        )

    _ = DriveWorkQueue(database)

    with closing(sqlite3.connect(database)) as db:
        columns = {
            row[1]
            for row in cast(
                "list[tuple[int, str, str, int, str | None, int]]",
                db.execute("PRAGMA table_info(agent_drive_work)").fetchall(),
            )
        }
        indexes = {
            row[1]
            for row in cast(
                "list[tuple[int, str, int, str, int]]",
                db.execute("PRAGMA index_list(agent_drive_work)").fetchall(),
            )
        }
        migrated = cast(
            "tuple[int, str, str, str | None, str | None] | None",
            db.execute(
                """SELECT revision,due_at,state,claim_owner,lease_expires_at
                FROM agent_drive_work WHERE tenant_id='tenant' AND run_id='run'"""
            ).fetchone(),
        )
    assert {"claim_owner", "lease_expires_at"} <= columns
    assert "agent_drive_work_claimable" in indexes
    assert migrated == (7, NOW.isoformat(), "pending", None, None)


def test_http_worker_drains_multiple_slices_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "web.sqlite3"
    adapter = FreshResearch()
    service = build_service(database, Steps(), research_adapter=adapter)
    jobs = AgentJobs(service)
    _ = MarketingAgentApi(service, "trace", "member", "test-secret", jobs=jobs)
    request = run_request()
    job = WebJob(
        job_id="web",
        run_id=request.run_id,
        action="create",
        goal=request.goal,
        budget=request.budget.model_copy(update={"max_tool_calls": 12}),
    )
    _ = jobs.enqueue("trace", "member", job, now=APP_NOW)
    assert jobs.work_once(now=APP_NOW)
    initial = service.repository.get("trace", request.run_id)
    assert initial is not None
    assert initial.state is AgentRunState.RUNNING
    restarted = AgentJobs(
        build_service(database, Steps(), research_adapter=adapter),
        drive_authorizer=lambda identity: identity.principal_id == "member",
    )
    restarted.recover()
    for _ in range(5):
        _ = restarted.work_once(now=APP_NOW)
    final = restarted.service.repository.get("trace", request.run_id)
    assert final is not None
    assert final.state is AgentRunState.COMPLETED
    assert len(adapter.inputs) == 10


def test_http_input_requeues_while_another_worker_holds_the_run_lease(tmp_path: Path) -> None:
    database = tmp_path / "http-contention.sqlite3"
    service = build_service(database, Steps())
    jobs = AgentJobs(service)
    run, _, _ = _admit_running(service.repository, jobs.drive_queue)
    active = jobs.drive_queue.claim("http", NOW)
    assert active is not None
    job = WebJob(
        job_id="lease-input-job",
        run_id=run.run_id,
        action="input",
        evidence={"note": "new authenticated correction"},
        expected_revision=run.revision,
    )

    assert jobs.enqueue(run.tenant_id, "member", job, now=NOW)["state"] == "pending"
    assert jobs.work_once(now=NOW)
    assert jobs.status(run.tenant_id, job.job_id)["state"] == "pending"
    assert service.repository.get(run.tenant_id, run.run_id) == run


def test_http_revoked_actor_cannot_resume_tools(tmp_path: Path) -> None:
    database = tmp_path / "revoked.sqlite3"
    adapter = FreshResearch()
    jobs = AgentJobs(
        build_service(database, Steps(), research_adapter=adapter),
        drive_authorizer=lambda identity: False,
    )
    request = run_request()
    _enqueue_bounded_http_create(jobs, request)
    assert jobs.work_once(now=APP_NOW)
    calls = len(adapter.inputs)
    assert jobs.work_once(now=APP_NOW)
    assert len(adapter.inputs) == calls
    assert jobs.status("trace", "web")["error"] == "actor_revoked"
    run = jobs.service.repository.get("trace", request.run_id)
    assert run is not None
    assert run.state is AgentRunState.BLOCKED


def test_missing_current_http_authority_records_explicit_block(tmp_path: Path) -> None:
    database = tmp_path / "unavailable.sqlite3"
    adapter = FreshResearch()
    jobs = AgentJobs(build_service(database, Steps(), research_adapter=adapter))
    request = run_request()
    _enqueue_bounded_http_create(jobs, request)
    assert jobs.work_once(now=APP_NOW)
    calls = len(adapter.inputs)
    assert jobs.work_once(now=APP_NOW)
    assert jobs.status("trace", "web")["error"] == "authorization_unavailable"
    assert len(adapter.inputs) == calls


def test_failed_transaction_leaves_no_runnable_work(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "rollback.sqlite3")
    queue = DriveWorkQueue(repository.database_path)
    run, task, _ = _bound_task(repository, queue)
    with pytest.raises(ValueError, match="revision_conflict"):
        _ = repository.append_step(
            run,
            step().model_copy(update={"sequence": 9}),
            state=AgentRunState.RUNNING,
            expected_revision=9,
            records=task_records(run, task, NOW),
            admission=queue.transition(run, task, NOW),
        )
    assert queue.claim("http", NOW) is None


def test_checkpoint_and_wake_rollback_after_queue_write(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "fault.sqlite3")
    queue = DriveWorkQueue(repository.database_path)
    run, task, _ = _bound_task(repository, queue)

    def fail(connection: Connection) -> None:
        queue.transition(run, task, NOW)(connection)
        message = "injected queue precommit failure"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="queue precommit"):
        _ = repository.append_step(
            run,
            step(),
            state=AgentRunState.RUNNING,
            expected_revision=1,
            records=task_records(run, task, NOW),
            admission=fail,
        )
    assert queue.claim("http", NOW) is None
    assert repository.get(run.tenant_id, run.run_id) == run


def test_pending_admitted_http_input_fences_terminal_commit(tmp_path: Path) -> None:
    service = build_service(tmp_path / "fence.sqlite3", Steps())
    jobs = AgentJobs(service)
    run = service.repository.create(make_run())
    jobs.drive_queue.bind(
        DriveOrigin(
            tenant_id=run.tenant_id,
            run_id=run.run_id,
            channel="http",
            principal_id="member",
            event_id="initial",
        )
    )
    _ = jobs.enqueue(
        run.tenant_id,
        "member",
        WebJob(
            job_id="correction",
            run_id=run.run_id,
            action="input",
            evidence={"note": "new requirement"},
            expected_revision=1,
        ),
        now=NOW,
    )
    task = seed_task(run)
    terminal = _terminal_task(task)
    with pytest.raises(DriveAdmissionConflict, match="pending_admission"):
        _ = service.repository.append_step(
            run,
            step(),
            state=AgentRunState.COMPLETED,
            expected_revision=1,
            records=task_records(run, terminal, NOW),
            admission=jobs.drive_queue.transition(run, terminal, NOW),
        )
    assert service.repository.get(run.tenant_id, run.run_id) == run
