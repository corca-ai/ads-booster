from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol, cast, override

from pydantic import TypeAdapter, ValidationError

from ads_booster.marketing.models import (
    ApprovalDecision,
    ApprovalPhase,
    MarketingTask,
    ReviewApproval,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from ads_booster.workspace import CandidateId, CandidateRecord, CandidateStatus, WorkspaceId

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_FILENAME: Final = "marketing-bridge.sqlite3"
_MAX_CANDIDATE_SELECTION: Final = 8
_CANDIDATE_IDS_ADAPTER = TypeAdapter(list[str])
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


class CandidateReviewStore(Protocol):
    def get_candidate(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
    ) -> CandidateRecord: ...


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
            self._record_review_run(connection, task, result, completed_at.timestamp())
        return callback

    def sync_review_approvals(self, store: CandidateReviewStore) -> int:
        """Queue approval events only after every candidate reaches its human-reviewed state."""
        with self._connect() as connection:
            rows = _fetchall(
                connection,
                """
                SELECT run_id, account_id, workspace_id, phase, candidate_ids_json
                FROM marketing_review_runs ORDER BY created_at
                """,
            )
        queued = 0
        for row in rows:
            run_id, account_id, workspace_id, phase_value, candidate_ids_json = map(str, row)
            candidate_ids = _candidate_ids_json(candidate_ids_json)
            if candidate_ids is None:
                continue
            try:
                records = tuple(
                    store.get_candidate(WorkspaceId(workspace_id), CandidateId(candidate_id))
                    for candidate_id in candidate_ids
                )
            except Exception:  # noqa: BLE001, S112 - review state must fail closed.
                continue
            phase = ApprovalPhase(phase_value)
            decision = _review_decision(phase, records)
            if decision is None:
                continue
            selected = (
                tuple(
                    str(record.candidate_id)
                    for record in records
                    if record.status is not CandidateStatus.REJECTED
                )
                if phase is ApprovalPhase.CANDIDATES and decision is ApprovalDecision.APPROVED
                else ()
            )
            approval = ReviewApproval(
                approval_id=f"{run_id}:{phase}",
                run_id=run_id,
                account_id=account_id,
                phase=phase,
                decision=decision,
                candidate_ids=selected,
                reviewed_at=datetime.now(UTC),
            )
            if self._enqueue_approval(approval):
                queued += 1
        return queued

    def pending_approvals(self, *, limit: int = 20) -> tuple[ReviewApproval, ...]:
        with self._connect() as connection:
            rows = _fetchall(
                connection,
                """
                SELECT approval_json FROM marketing_approval_outbox
                WHERE delivered_at IS NULL ORDER BY created_at LIMIT ?
                """,
                (limit,),
            )
        return tuple(ReviewApproval.model_validate_json(str(row[0])) for row in rows)

    def record_approval_attempt(self, approval_id: str) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            _ = connection.execute(
                """
                UPDATE marketing_approval_outbox
                SET attempts = attempts + 1, updated_at = ? WHERE approval_id = ?
                """,
                (now, approval_id),
            )

    def mark_approval_delivered(self, approval_id: str) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE marketing_approval_outbox SET delivered_at = ?, updated_at = ?
                WHERE approval_id = ? AND delivered_at IS NULL
                """,
                (now, now, approval_id),
            )
            if result.rowcount not in (0, 1):
                raise InboxStateError(f"invalid approval update for {approval_id!r}")

    @staticmethod
    def _record_review_run(
        connection: sqlite3.Connection,
        task: MarketingTask,
        result: TaskResult,
        now: float,
    ) -> None:
        if result.status is not TaskStatus.SUCCEEDED or task.kind not in {
            TaskKind.GENERATE_CANDIDATES,
            TaskKind.CAPTURE,
        }:
            return
        workspace_id = task.payload.get("workspace_id")
        raw_ids = (
            result.output.get("candidate_ids")
            if task.kind is TaskKind.GENERATE_CANDIDATES
            else task.payload.get("candidate_ids")
        )
        candidate_ids = _candidate_ids(raw_ids)
        if not isinstance(workspace_id, str) or not workspace_id or candidate_ids is None:
            return
        phase = (
            ApprovalPhase.CANDIDATES
            if task.kind is TaskKind.GENERATE_CANDIDATES
            else ApprovalPhase.PUBLICATION
        )
        _ = connection.execute(
            """
            INSERT INTO marketing_review_runs (
                run_id, account_id, workspace_id, phase, candidate_ids_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                account_id = excluded.account_id,
                workspace_id = excluded.workspace_id,
                phase = excluded.phase,
                candidate_ids_json = excluded.candidate_ids_json,
                updated_at = excluded.updated_at
            """,
            (
                task.run_id,
                task.account_id,
                workspace_id,
                phase,
                _CANDIDATE_IDS_ADAPTER.dump_json(list(candidate_ids)).decode(),
                now,
                now,
            ),
        )

    def _enqueue_approval(self, approval: ReviewApproval) -> bool:
        now = approval.reviewed_at.timestamp()
        with self._connect(write=True) as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO marketing_approval_outbox (
                    approval_id, run_id, approval_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.run_id,
                    approval.model_dump_json(),
                    now,
                    now,
                ),
            )
        return result.rowcount == 1

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


def _candidate_ids(value: object) -> tuple[str, ...] | None:
    try:
        values = _CANDIDATE_IDS_ADAPTER.validate_python(value)
    except ValidationError:
        return None
    if not values or len(values) > _MAX_CANDIDATE_SELECTION or any(not item for item in values):
        return None
    return tuple(dict.fromkeys(values))


def _candidate_ids_json(value: str) -> tuple[str, ...] | None:
    try:
        values = _CANDIDATE_IDS_ADAPTER.validate_json(value)
    except ValidationError:
        return None
    return _candidate_ids(values)


def _review_decision(
    phase: ApprovalPhase,
    records: tuple[CandidateRecord, ...],
) -> ApprovalDecision | None:
    if phase is ApprovalPhase.PUBLICATION:
        return (
            ApprovalDecision.APPROVED
            if records and all(record.status is CandidateStatus.SUBMITTED for record in records)
            else None
        )
    if any(record.status is CandidateStatus.AWAITING_REVIEW for record in records):
        return None
    accepted = tuple(record for record in records if record.status is not CandidateStatus.REJECTED)
    return ApprovalDecision.APPROVED if accepted else ApprovalDecision.REJECTED


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
