from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import uuid4

from trace_capture.automation.database import AutomationDatabase
from trace_capture.automation.errors import (
    DuplicateIdempotencyError,
    InvalidQueueCompletionError,
    QueueNotFoundError,
    QueueRevisionError,
)
from trace_capture.automation.models import (
    QueueCompletion,
    QueueId,
    QueueRecord,
    QueueState,
    QueueSubmission,
)
from trace_capture.automation.queries import (
    CLAIM,
    EXHAUST_CLAIM,
    EXPIRE_RUNNING,
    FINISH,
    INSERT_SUBMITTED,
    RECOVER_CLAIM,
    REVIEW,
    SELECT_ACTIVE,
    SELECT_DUE,
    SELECT_ID,
    SELECT_IDEMPOTENCY,
    SELECT_SCOPED,
    SELECT_WORKSPACE,
    START,
)
from trace_capture.automation.rows import parse_row, queue_record

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from trace_capture.workspace import WorkspaceId
    from trace_capture.workspace.database import SqliteCursor, SqliteRow


class AutomationQueue:
    _database: AutomationDatabase

    def __init__(self, home: Path) -> None:
        """Open the durable queue inside the configured agent home."""
        self._database = AutomationDatabase(home)

    @property
    def database_path(self) -> Path:
        return self._database.path

    def enqueue(self, submission: QueueSubmission) -> QueueRecord:
        payload = submission.bundle.model_dump_json()
        digest = sha256(submission.model_dump_json().encode()).hexdigest()
        now = datetime.now(submission.due_at.tzinfo)
        queue_id = QueueId(uuid4().hex)
        with self._database.connect(write=True) as connection:
            existing = parse_row(
                _fetchone(
                    connection,
                    SELECT_IDEMPOTENCY,
                    (submission.workspace_id, submission.idempotency_key),
                )
            )
            if existing is not None:
                record = queue_record(existing)
                if record.payload_digest == digest:
                    return record
                raise DuplicateIdempotencyError(submission.idempotency_key)
            _ = connection.execute(
                INSERT_SUBMITTED,
                (
                    queue_id,
                    submission.workspace_id,
                    submission.idempotency_key,
                    digest,
                    payload,
                    submission.due_at.timestamp(),
                    submission.max_attempts,
                    now.timestamp(),
                    now.timestamp(),
                ),
            )
        return self.get(submission.workspace_id, queue_id)

    def get(self, workspace_id: WorkspaceId, queue_id: QueueId) -> QueueRecord:
        with self._database.connect() as connection:
            row = parse_row(
                _fetchone(
                    connection,
                    SELECT_SCOPED,
                    (workspace_id, queue_id),
                )
            )
        if row is None:
            raise QueueNotFoundError(queue_id)
        return queue_record(row)

    def list_workspace(self, workspace_id: WorkspaceId) -> tuple[QueueRecord, ...]:
        with self._database.connect() as connection:
            rows = _fetchall(connection, SELECT_WORKSPACE, (workspace_id,))
        records: list[QueueRecord] = []
        for raw in rows:
            row = parse_row(raw)
            if row is not None:
                records.append(queue_record(row))
        return tuple(records)

    def claim_due(
        self, *, worker_id: str, now: datetime, lease_seconds: float
    ) -> QueueRecord | None:
        timestamp = now.timestamp()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                EXPIRE_RUNNING,
                (timestamp, timestamp),
            )
            _ = connection.execute(
                EXHAUST_CLAIM,
                (timestamp, timestamp),
            )
            _ = connection.execute(
                RECOVER_CLAIM,
                (timestamp, timestamp),
            )
            active = _fetchone(
                connection,
                SELECT_ACTIVE,
            )
            if active is not None:
                return None
            candidate = _fetchone(
                connection,
                SELECT_DUE,
                (timestamp,),
            )
            match candidate:
                case (str() as queue_id,):
                    lease_until = (now + timedelta(seconds=lease_seconds)).timestamp()
                    _ = connection.execute(
                        CLAIM,
                        (worker_id, lease_until, timestamp, queue_id),
                    )
                    row = parse_row(
                        _fetchone(
                            connection,
                            SELECT_ID,
                            (queue_id,),
                        )
                    )
                case None:
                    return None
                case _:
                    msg = "automation queue candidate row is corrupt"
                    raise RuntimeError(msg)
        return None if row is None else queue_record(row)

    def start(
        self,
        queue_id: QueueId,
        *,
        worker_id: str,
        expected_revision: int,
        now: datetime,
    ) -> QueueRecord:
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                START,
                (now.timestamp(), queue_id, worker_id, expected_revision),
            )
            if result.rowcount != 1:
                raise QueueRevisionError(queue_id, expected_revision)
        return self._get_by_id(queue_id)

    def finish(
        self,
        running: QueueRecord,
        *,
        completion: QueueCompletion,
        now: datetime,
    ) -> QueueRecord:
        match completion.state:
            case QueueState.REVIEW | QueueState.FAILED:
                pass
            case invalid_state:
                raise InvalidQueueCompletionError(invalid_state)
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                FINISH,
                (
                    completion.state,
                    completion.run_id,
                    completion.run_idempotency_key,
                    completion.artifact_path,
                    completion.artifact_sha256,
                    completion.failure_code,
                    now.timestamp(),
                    running.queue_id,
                    running.worker_id,
                    running.revision,
                ),
            )
            if result.rowcount != 1:
                raise QueueRevisionError(running.queue_id, running.revision)
        return self._get_by_id(running.queue_id)

    def review(
        self,
        queue_id: QueueId,
        *,
        workspace_id: WorkspaceId,
        accepted: bool,
        expected_revision: int,
        now: datetime,
    ) -> QueueRecord:
        state = QueueState.ACCEPTED if accepted else QueueState.REJECTED
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                REVIEW,
                (state, now.timestamp(), queue_id, workspace_id, expected_revision),
            )
            if result.rowcount != 1:
                raise QueueRevisionError(queue_id, expected_revision)
        return self.get(workspace_id, queue_id)

    def _get_by_id(self, queue_id: QueueId) -> QueueRecord:
        with self._database.connect() as connection:
            row = parse_row(
                _fetchone(
                    connection,
                    SELECT_ID,
                    (queue_id,),
                )
            )
        if row is None:
            raise QueueNotFoundError(queue_id)
        return queue_record(row)


def _fetchone(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[bytes | float | int | str | None, ...] = (),
) -> SqliteRow | None:
    cursor: SqliteCursor = connection.execute(query, parameters)
    return _cursor_fetchone(cursor)


def _fetchall(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[bytes | float | int | str | None, ...] = (),
) -> list[SqliteRow]:
    cursor: SqliteCursor = connection.execute(query, parameters)
    return cursor.fetchall()


def _cursor_fetchone(cursor: SqliteCursor) -> SqliteRow | None:
    return cursor.fetchone()
