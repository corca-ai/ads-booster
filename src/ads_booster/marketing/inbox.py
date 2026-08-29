"""# noqa: SIZE_OK - SQLite inbox task and callback transitions share one schema."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final, cast, override

from ads_booster.marketing.models import (
    MarketingTask,
    TaskCallback,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_FILENAME: Final = "marketing-bridge.sqlite3"
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS marketing_inbox (
    task_id TEXT PRIMARY KEY,
    body_digest TEXT NOT NULL,
    task_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('received', 'running', 'completed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    job_digest TEXT,
    export_nonce TEXT,
    workspace_id TEXT,
    execution_started_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS marketing_inbox_ready
ON marketing_inbox (state, created_at);
CREATE TABLE IF NOT EXISTS marketing_outbox (
    callback_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES marketing_inbox(task_id),
    callback_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS marketing_outbox_pending
ON marketing_outbox (delivered_at, created_at);
CREATE TABLE IF NOT EXISTS marketing_review_runs (
    run_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('candidates', 'publication')),
    candidate_ids_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS marketing_approval_outbox (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES marketing_review_runs(run_id),
    approval_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS marketing_approval_outbox_pending
ON marketing_approval_outbox (delivered_at, created_at);
"""
_ADMISSION_COLUMNS: Final = (
    ("job_digest", "TEXT"),
    ("export_nonce", "TEXT"),
    ("workspace_id", "TEXT"),
    ("execution_started_at", "REAL"),
)


class InboxConflictError(RuntimeError):
    task_id: str

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task {task_id!r} was redelivered with a different payload")


class InboxStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionAdmission:
    job_digest: str
    export_nonce: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class RecoverySummary:
    requeued: int
    unknown_side_effects: int


@dataclass(frozen=True, slots=True)
class InboxQuiescence:
    received_tasks: int
    running_tasks: int
    guarded_tasks: int
    pending_callbacks: int

    @property
    def ready(self) -> bool:
        return not any(
            (
                self.received_tasks,
                self.running_tasks,
                self.guarded_tasks,
                self.pending_callbacks,
            )
        )


class MarketingInbox:
    path: Path

    def __init__(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / _FILENAME
        with self._connect(write=True) as connection:
            _ = connection.executescript(_SCHEMA)
            self._migrate(connection)
        self.path.chmod(0o600)

    def ingest(self, task: MarketingTask) -> bool:
        serialized = task.model_dump_json()
        digest = sha256(serialized.encode()).hexdigest()
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            row = _fetchone(
                connection,
                "SELECT body_digest FROM marketing_inbox WHERE task_id = ?",
                (task.task_id,),
            )
            if row is not None:
                existing_digest = str(row[0])
                if existing_digest != digest:
                    raise InboxConflictError(task.task_id)
                return False
            _ = connection.execute(
                """
                INSERT INTO marketing_inbox
                    (task_id, body_digest, task_json, state, created_at, updated_at)
                VALUES (?, ?, ?, 'received', ?, ?)
                """,
                (task.task_id, digest, serialized, now, now),
            )
        return True

    def claim_next(self) -> MarketingTask | None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            row = _fetchone(
                connection,
                """
                SELECT task_id, task_json FROM marketing_inbox
                WHERE state = 'received' ORDER BY created_at LIMIT 1
                """,
            )
            if row is None:
                return None
            task_id, task_json = str(row[0]), str(row[1])
            result = connection.execute(
                """
                UPDATE marketing_inbox
                SET state = 'running', attempts = attempts + 1, updated_at = ?
                WHERE task_id = ? AND state = 'received'
                """,
                (now, task_id),
            )
            if result.rowcount != 1:
                raise InboxStateError(f"failed to claim task {task_id!r}")
        return MarketingTask.model_validate_json(task_json)

    def recover_running(self) -> int:
        return self.recover_interrupted().requeued

    def begin_execution(self, task_id: str, admission: ExecutionAdmission) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE marketing_inbox
                SET job_digest = ?, export_nonce = ?, workspace_id = ?,
                    execution_started_at = ?, updated_at = ?
                WHERE task_id = ? AND state = 'running'
                    AND job_digest IS NULL AND export_nonce IS NULL
                    AND workspace_id IS NULL AND execution_started_at IS NULL
                """,
                (
                    admission.job_digest,
                    admission.export_nonce,
                    admission.workspace_id,
                    now,
                    now,
                    task_id,
                ),
            )
            if result.rowcount != 1:
                raise InboxStateError(f"cannot admit execution for task {task_id!r}")

    def execution_admission(self, task_id: str) -> ExecutionAdmission | None:
        with self._connect() as connection:
            row = _fetchone(
                connection,
                """
                SELECT job_digest, export_nonce, workspace_id, execution_started_at
                FROM marketing_inbox WHERE task_id = ?
                """,
                (task_id,),
            )
        if row is None:
            raise InboxStateError(f"unknown task {task_id!r}")
        job_digest, export_nonce, workspace_id, execution_started_at = row
        if execution_started_at is None:
            return None
        if not all(isinstance(value, str) and value for value in row[:3]):
            raise InboxStateError(f"task {task_id!r} has an incomplete execution admission")
        return ExecutionAdmission(
            job_digest=str(job_digest),
            export_nonce=str(export_nonce),
            workspace_id=str(workspace_id),
        )

    def recover_interrupted(self) -> RecoverySummary:
        now = datetime.now(UTC)
        with self._connect(write=True) as connection:
            requeued = connection.execute(
                """
                UPDATE marketing_inbox SET state = 'received', updated_at = ?
                WHERE state = 'running' AND execution_started_at IS NULL
                """,
                (now.timestamp(),),
            ).rowcount
            guarded_rows = _fetchall(
                connection,
                """
                SELECT task_json FROM marketing_inbox
                WHERE state = 'running' AND execution_started_at IS NOT NULL
                ORDER BY created_at
                """,
            )
            for row in guarded_rows:
                task = MarketingTask.model_validate_json(str(row[0]))
                _ = self._complete_in_transaction(
                    connection,
                    task,
                    TaskResult(
                        status=TaskStatus.UNKNOWN_SIDE_EFFECT,
                        failure_code="native_appium_side_effect_unknown",
                    ),
                    now,
                )
        return RecoverySummary(requeued=requeued, unknown_side_effects=len(guarded_rows))

    def quiescence(self) -> InboxQuiescence:
        with self._connect() as connection:
            row = _fetchone(
                connection,
                """
                SELECT
                    (SELECT COUNT(*) FROM marketing_inbox WHERE state = 'received'),
                    (SELECT COUNT(*) FROM marketing_inbox WHERE state = 'running'),
                    (SELECT COUNT(*) FROM marketing_inbox
                        WHERE state = 'running' AND execution_started_at IS NOT NULL),
                    (SELECT COUNT(*) FROM marketing_outbox WHERE delivered_at IS NULL)
                """,
            )
        if row is None:
            raise InboxStateError("worker inbox quiescence query returned no row")
        return InboxQuiescence(*(int(value or 0) for value in row))

    def complete(self, task: MarketingTask, result: TaskResult) -> TaskCallback:
        completed_at = datetime.now(UTC)
        with self._connect(write=True) as connection:
            return self._complete_in_transaction(connection, task, result, completed_at)

    @staticmethod
    def _complete_in_transaction(
        connection: sqlite3.Connection,
        task: MarketingTask,
        result: TaskResult,
        completed_at: datetime,
    ) -> TaskCallback:
        callback = TaskCallback(
            callback_id=f"{task.task_id}:completed",
            task_id=task.task_id,
            run_id=task.run_id,
            account_id=task.account_id,
            kind=task.kind,
            result=result,
            completed_at=completed_at,
        )
        updated = connection.execute(
            """
                UPDATE marketing_inbox SET state = 'completed', updated_at = ?
                WHERE task_id = ? AND state = 'running'
                """,
            (completed_at.timestamp(), task.task_id),
        )
        if updated.rowcount != 1:
            existing = _fetchone(
                connection,
                "SELECT state FROM marketing_inbox WHERE task_id = ?",
                (task.task_id,),
            )
            if existing is None or str(existing[0]) != "completed":
                raise InboxStateError(f"cannot complete task {task.task_id!r}")
        _ = connection.execute(
            """
                INSERT OR IGNORE INTO marketing_outbox
                    (callback_id, task_id, callback_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
            (
                callback.callback_id,
                task.task_id,
                callback.model_dump_json(),
                completed_at.timestamp(),
                completed_at.timestamp(),
            ),
        )
        return callback

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1]) for row in _fetchall(connection, "PRAGMA table_info(marketing_inbox)")
        }
        for name, sql_type in _ADMISSION_COLUMNS:
            if name not in columns:
                _ = connection.execute(f"ALTER TABLE marketing_inbox ADD COLUMN {name} {sql_type}")

    def pending_callbacks(self, *, limit: int = 20) -> tuple[TaskCallback, ...]:
        with self._connect() as connection:
            rows = _fetchall(
                connection,
                """
                SELECT callback_json FROM marketing_outbox
                WHERE delivered_at IS NULL ORDER BY created_at LIMIT ?
                """,
                (limit,),
            )
        return tuple(TaskCallback.model_validate_json(str(row[0])) for row in rows)

    def mark_callback_delivered(self, callback_id: str) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE marketing_outbox SET delivered_at = ?, updated_at = ?
                WHERE callback_id = ? AND delivered_at IS NULL
                """,
                (now, now, callback_id),
            )
            if result.rowcount not in (0, 1):
                raise InboxStateError(f"invalid callback update for {callback_id!r}")

    def record_callback_attempt(self, callback_id: str) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            _ = connection.execute(
                """
                UPDATE marketing_outbox
                SET attempts = attempts + 1, updated_at = ? WHERE callback_id = ?
                """,
                (now, callback_id),
            )

    @contextmanager
    def _connect(self, *, write: bool = False) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            _ = connection.execute("PRAGMA foreign_keys = ON")
            _ = connection.execute("PRAGMA busy_timeout = 30000")
            if write:
                _ = connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()


type SqliteValue = bytes | float | int | str | None
type SqliteRow = tuple[SqliteValue, ...]


def _fetchone(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[SqliteValue, ...] = (),
) -> SqliteRow | None:
    cursor = connection.execute(query, parameters)
    return cast("SqliteRow | None", cursor.fetchone())


def _fetchall(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[SqliteValue, ...] = (),
) -> list[SqliteRow]:
    cursor = connection.execute(query, parameters)
    return cast("list[SqliteRow]", cursor.fetchall())


class MarketingExecutionError(RuntimeError):
    failure_code: str
    unknown_side_effect: bool

    def __init__(
        self,
        failure_code: str,
        *,
        unknown_side_effect: bool = False,
    ) -> None:
        self.failure_code = failure_code
        self.unknown_side_effect = unknown_side_effect
        super().__init__(failure_code)

    @override
    def __str__(self) -> str:
        return self.failure_code
