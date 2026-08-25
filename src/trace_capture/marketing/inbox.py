from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final, cast, override

from trace_capture.marketing.models import MarketingTask, TaskCallback, TaskResult

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
"""


class InboxConflictError(RuntimeError):
    task_id: str

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task {task_id!r} was redelivered with a different payload")


class InboxStateError(RuntimeError):
    pass


class MarketingInbox:
    path: Path

    def __init__(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / _FILENAME
        with self._connect(write=True) as connection:
            _ = connection.executescript(_SCHEMA)
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
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE marketing_inbox SET state = 'received', updated_at = ?
                WHERE state = 'running'
                """,
                (now,),
            )
        return result.rowcount

    def complete(self, task: MarketingTask, result: TaskResult) -> TaskCallback:
        completed_at = datetime.now(UTC)
        callback = TaskCallback(
            callback_id=f"{task.task_id}:completed",
            task_id=task.task_id,
            run_id=task.run_id,
            account_id=task.account_id,
            kind=task.kind,
            result=result,
            completed_at=completed_at,
        )
        with self._connect(write=True) as connection:
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
