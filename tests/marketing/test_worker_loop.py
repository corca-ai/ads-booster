"""# noqa: SIZE_OK - Worker-loop cases share stateful broker fixtures and event ordering."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.errors import CloudflareQueueError
from ads_booster.marketing.inbox import (
    ExecutionAdmission,
    InboxStateError,
    MarketingExecutionError,
    MarketingInbox,
)
from ads_booster.marketing.models import (
    MarketingTask,
    QueueLease,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from ads_booster.marketing.worker_loop import MarketingWorkerLoop

if TYPE_CHECKING:
    from pathlib import Path


def _task() -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="hosted:task-1",
        payload={"workspace_id": "workspace-1"},
        created_at=datetime.now(UTC),
    )


@dataclass(frozen=True, slots=True)
class PreparedCapture:
    execution_admission: ExecutionAdmission


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeBroker:
    """Mutable fixture advances leases and records delivery outcomes across a worker tick."""

    inbox: MarketingInbox
    leases: tuple[QueueLease, ...]
    events: list[str] = field(default_factory=list)
    callbacks: list[TaskCallback] = field(default_factory=list)
    callback_failures: int = 0
    barrier_failures: int = 0

    def pull(self) -> tuple[QueueLease, ...]:
        leases, self.leases = self.leases, ()
        return leases

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None:
        assert not retry_lease_ids
        with closing(sqlite3.connect(self.inbox.path)) as connection:
            assert connection.execute(
                "SELECT state FROM marketing_inbox WHERE task_id = ?",
                (_task().task_id,),
            ).fetchone() == ("received",)
        self.events.append(f"ack:{','.join(ack_lease_ids)}")

    def mark_execution_started(self, task_id: str) -> None:
        assert self.inbox.execution_admission(task_id) is not None
        self.events.append("barrier")
        if self.barrier_failures:
            self.barrier_failures -= 1
            message = "barrier unavailable"
            raise CloudflareQueueError(message)

    def deliver(self, callback: TaskCallback) -> None:
        self.events.append("callback")
        if self.callback_failures:
            self.callback_failures -= 1
            message = "callback unavailable"
            raise CloudflareQueueError(message)
        self.callbacks.append(callback)


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakePreparer:
    """Mutable fixture records preparation events and exposes an injected failure code."""

    events: list[str]
    failure_code: str | None = None

    def prepare(self, task: MarketingTask) -> PreparedCapture:
        self.events.append("prepare")
        if self.failure_code is not None:
            raise MarketingExecutionError(self.failure_code)
        return PreparedCapture(
            execution_admission=ExecutionAdmission(
                job_digest="a" * 64,
                export_nonce="nonce-1",
                workspace_id=str(task.payload["workspace_id"]),
            )
        )


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeExecutor:
    """Mutable fixture counts execution calls and exposes an injected post-barrier failure."""

    events: list[str]
    calls: int = 0
    fail_after_start: bool = False
    known_unknown_failure: str | None = None

    def execute(self, prepared: PreparedCapture) -> TaskResult:
        assert prepared.execution_admission.job_digest == "a" * 64
        self.events.append("execute")
        self.calls += 1
        if self.known_unknown_failure is not None:
            raise MarketingExecutionError(
                self.known_unknown_failure,
                unknown_side_effect=True,
            )
        if self.fail_after_start:
            message = "executor interrupted"
            raise RuntimeError(message)
        return TaskResult(status=TaskStatus.SUCCEEDED)


def _loop(
    tmp_path: Path,
    *,
    callback_failures: int = 0,
    barrier_failures: int = 0,
    fail_after_start: bool = False,
    preparation_failure: str | None = None,
) -> tuple[MarketingWorkerLoop[PreparedCapture], FakeBroker, FakeExecutor]:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    broker = FakeBroker(
        inbox=inbox,
        leases=(QueueLease(message_id="message-1", lease_id="lease-1", attempts=1, task=task),),
        callback_failures=callback_failures,
        barrier_failures=barrier_failures,
    )
    executor = FakeExecutor(
        broker.events,
        fail_after_start=fail_after_start,
    )
    worker = MarketingWorkerLoop(
        broker=broker,
        inbox=inbox,
        preparer=FakePreparer(broker.events, failure_code=preparation_failure),
        executor=executor,
    )
    return worker, broker, executor


def test_worker_loop_orders_durable_ingest_ack_prepare_barrier_execute_and_callback(
    tmp_path: Path,
) -> None:
    worker, broker, executor = _loop(tmp_path)

    assert worker.tick()

    assert executor.calls == 1
    assert broker.events == ["ack:lease-1", "prepare", "barrier", "execute", "callback"]
    assert [callback.result.status for callback in broker.callbacks] == [TaskStatus.SUCCEEDED]
    assert worker.inbox.quiescence().ready


def test_callback_retry_never_reexecutes_admitted_task(tmp_path: Path) -> None:
    worker, broker, executor = _loop(tmp_path, callback_failures=1)

    assert worker.tick()
    assert executor.calls == 1
    assert worker.inbox.quiescence().pending_callbacks == 1

    assert worker.tick()
    assert executor.calls == 1
    assert len(broker.callbacks) == 1


def test_barrier_failure_fails_closed_without_starting_executor(tmp_path: Path) -> None:
    worker, broker, executor = _loop(tmp_path, barrier_failures=1)

    assert worker.tick()

    assert executor.calls == 0
    assert broker.callbacks[0].result.status is TaskStatus.UNKNOWN_SIDE_EFFECT
    assert worker.inbox.recover_interrupted().unknown_side_effects == 0


def test_preparation_failure_completes_before_barrier_and_executor(tmp_path: Path) -> None:
    worker, broker, executor = _loop(
        tmp_path,
        preparation_failure="native_capture_trace_items_invalid",
    )

    assert worker.tick()

    assert executor.calls == 0
    assert broker.events == ["ack:lease-1", "prepare", "callback"]
    assert broker.callbacks[0].result.status is TaskStatus.FAILED
    assert broker.callbacks[0].result.failure_code == "native_capture_trace_items_invalid"


def test_local_admission_is_immutable_before_barrier(tmp_path: Path) -> None:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    assert inbox.ingest(task)
    assert inbox.claim_next() == task
    original = ExecutionAdmission(
        job_digest="d" * 64,
        export_nonce="nonce-original",
        workspace_id="workspace-1",
    )
    inbox.begin_execution(task.task_id, original)

    with pytest.raises(InboxStateError, match="cannot admit execution"):
        inbox.begin_execution(
            task.task_id,
            ExecutionAdmission(
                job_digest="e" * 64,
                export_nonce="nonce-replacement",
                workspace_id="workspace-2",
            ),
        )

    assert inbox.execution_admission(task.task_id) == original


def test_post_barrier_interrupted_execution_is_unknown_side_effect_and_not_reexecuted(
    tmp_path: Path,
) -> None:
    worker, broker, executor = _loop(tmp_path, fail_after_start=True)

    assert worker.tick()

    assert executor.calls == 1
    assert broker.callbacks[0].result.status is TaskStatus.UNKNOWN_SIDE_EFFECT
    assert worker.inbox.recover_interrupted().unknown_side_effects == 0


def test_post_barrier_capture_validation_failure_is_unknown_and_not_reexecuted(
    tmp_path: Path,
) -> None:
    worker, broker, executor = _loop(tmp_path)
    executor.known_unknown_failure = "native_capture_artifact_digest_mismatch"

    assert worker.tick()
    assert executor.calls == 1
    assert broker.callbacks[0].result.status is TaskStatus.UNKNOWN_SIDE_EFFECT
    assert broker.callbacks[0].result.failure_code == "native_capture_artifact_digest_mismatch"

    assert worker.tick() is False
    assert executor.calls == 1


def test_interrupted_unadmitted_task_is_requeued(tmp_path: Path) -> None:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    assert inbox.ingest(task)
    assert inbox.claim_next() == task

    recovered = inbox.recover_interrupted()

    assert recovered.requeued == 1
    assert recovered.unknown_side_effects == 0
    assert inbox.claim_next() == task


def test_interrupted_guarded_execution_creates_one_unknown_side_effect_callback_without_replay(
    tmp_path: Path,
) -> None:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    assert inbox.ingest(task)
    assert inbox.claim_next() == task
    inbox.begin_execution(
        task.task_id,
        ExecutionAdmission(
            job_digest="b" * 64,
            export_nonce="nonce-2",
            workspace_id="workspace-1",
        ),
    )

    first = MarketingInbox(tmp_path).recover_interrupted()
    second = MarketingInbox(tmp_path).recover_interrupted()

    assert first.unknown_side_effects == 1
    assert second.unknown_side_effects == 0
    assert inbox.claim_next() is None
    callbacks = inbox.pending_callbacks()
    assert len(callbacks) == 1
    assert callbacks[0].result.status is TaskStatus.UNKNOWN_SIDE_EFFECT
    assert callbacks[0].result.failure_code == "native_appium_side_effect_unknown"


def test_existing_database_is_additively_migrated_without_losing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "marketing-bridge.sqlite3"
    received = _task()
    running = received.model_copy(
        update={"task_id": "task-running", "idempotency_key": "hosted:task-running"}
    )
    completed = received.model_copy(
        update={"task_id": "task-completed", "idempotency_key": "hosted:task-completed"}
    )
    callback = TaskCallback(
        callback_id="task-completed:completed",
        task_id=completed.task_id,
        run_id=completed.run_id,
        account_id=completed.account_id,
        kind=completed.kind,
        result=TaskResult(status=TaskStatus.SUCCEEDED),
        completed_at=datetime.now(UTC),
    )
    with closing(sqlite3.connect(database)) as connection:
        _ = connection.executescript(
            """
            CREATE TABLE marketing_inbox (
                task_id TEXT PRIMARY KEY,
                body_digest TEXT NOT NULL,
                task_json TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE marketing_outbox (
                callback_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                callback_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE marketing_review_runs (
                run_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE marketing_approval_outbox (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                approval_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        _ = connection.executemany(
            "INSERT INTO marketing_inbox VALUES (?, ?, ?, ?, 0, ?, ?)",
            (
                (received.task_id, "digest-received", received.model_dump_json(), "received", 1, 1),
                (running.task_id, "digest-running", running.model_dump_json(), "running", 2, 2),
                (
                    completed.task_id,
                    "digest-completed",
                    completed.model_dump_json(),
                    "completed",
                    3,
                    3,
                ),
            ),
        )
        _ = connection.execute(
            "INSERT INTO marketing_outbox VALUES (?, ?, ?, 0, NULL, 3, 3)",
            (callback.callback_id, completed.task_id, callback.model_dump_json()),
        )
        connection.commit()

    inbox = MarketingInbox(tmp_path)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            """
            SELECT task_id, state, job_digest, export_nonce, workspace_id, execution_started_at
            FROM marketing_inbox ORDER BY created_at
            """,
        ).fetchall() == [
            ("task-1", "received", None, None, None, None),
            ("task-running", "running", None, None, None, None),
            ("task-completed", "completed", None, None, None, None),
        ]
        assert connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name IN ('marketing_review_runs', 'marketing_approval_outbox')
            """
        ).fetchone() == (2,)
    assert inbox.pending_callbacks() == (callback,)


def test_legacy_approval_rows_do_not_block_worker_quiescence(tmp_path: Path) -> None:
    # Given an inbox that still contains approval rows from the removed local workspace runtime
    inbox = MarketingInbox(tmp_path)
    with closing(sqlite3.connect(inbox.path)) as connection:
        _ = connection.execute(
            """
            INSERT INTO marketing_review_runs VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-legacy", "account-1", "workspace-1", "publication", "[]", 1, 1),
        )
        _ = connection.execute(
            """
            INSERT INTO marketing_approval_outbox VALUES (?, ?, ?, 0, NULL, ?, ?)
            """,
            ("approval-legacy", "run-legacy", "{}", 1, 1),
        )
        connection.commit()

    # When the retained worker checks whether durable work is drained
    snapshot = inbox.quiescence()

    # Then legacy local-review data remains readable but no longer participates in runtime state
    assert snapshot.ready
    with closing(sqlite3.connect(inbox.path)) as connection:
        assert connection.execute(
            "SELECT approval_id FROM marketing_approval_outbox"
        ).fetchall() == [("approval-legacy",)]
