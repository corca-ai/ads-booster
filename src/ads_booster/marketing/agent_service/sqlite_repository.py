"""Single-writer SQLite repository for canonical on-premises Agent Runs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRun,
    AgentRunEvent,
    AgentRunState,
    AgentStep,
    contract_sha256,
)

_SCHEMA_VERSION = 1
_MAX_LIST_LIMIT = 200

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_INTEGER = TypeAdapter(int)
_STRING = TypeAdapter(str)


class AgentRunConflictError(ValueError):
    """A requested mutation does not match the canonical run revision or lineage."""


@dataclass(frozen=True, slots=True)
class SqliteAgentRunRepository:
    database_path: Path

    def __post_init__(self) -> None:
        """Create the private database and apply the initial append-only schema."""
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connection() as connection:
            _ = connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS agent_schema (
                    version INTEGER PRIMARY KEY
                );
                INSERT OR IGNORE INTO agent_schema(version) VALUES (1);
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    head_step_sha256 TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE RESTRICT,
                    sequence INTEGER NOT NULL,
                    step_sha256 TEXT NOT NULL,
                    step_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence),
                    UNIQUE(run_id, step_sha256)
                );
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE RESTRICT,
                    sequence INTEGER NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    UNIQUE(run_id, event_sha256)
                );
                CREATE TABLE IF NOT EXISTS agent_records (
                    record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE RESTRICT,
                    kind TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(run_id, record_sha256)
                );
                CREATE TABLE IF NOT EXISTS agent_tool_idempotency (
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE RESTRICT,
                    invocation_sha256 TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, idempotency_key)
                );
                CREATE TRIGGER IF NOT EXISTS agent_steps_immutable
                BEFORE UPDATE ON agent_steps
                BEGIN
                    SELECT RAISE(ABORT, 'agent steps are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_steps_append_only
                BEFORE DELETE ON agent_steps
                BEGIN
                    SELECT RAISE(ABORT, 'agent steps are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_records_immutable
                BEFORE UPDATE ON agent_records
                BEGIN
                    SELECT RAISE(ABORT, 'agent records are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_records_append_only
                BEFORE DELETE ON agent_records
                BEGIN
                    SELECT RAISE(ABORT, 'agent records are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_run_events_immutable
                BEFORE UPDATE ON agent_run_events
                BEGIN
                    SELECT RAISE(ABORT, 'agent run events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_run_events_append_only
                BEFORE DELETE ON agent_run_events
                BEGIN
                    SELECT RAISE(ABORT, 'agent run events are append-only');
                END;
                """
            )
            version_row = cast(
                "tuple[object, ...] | None",
                connection.execute("SELECT MAX(version) FROM agent_schema").fetchone(),
            )
            version = None if version_row is None else _INTEGER.validate_python(version_row[0])
            if version != _SCHEMA_VERSION:
                raise AgentRunConflictError("agent_schema_version_unsupported")
        self.database_path.chmod(0o600)

    def create(self, run: AgentRun, *, request_sha256: str | None = None) -> AgentRun:
        create_sha256 = contract_sha256(run) if request_sha256 is None else request_sha256
        run_json = run.model_dump_json()
        event = AgentRunEvent(
            schema_version="trace.agent-run-event.v1",
            run_id=run.run_id,
            sequence=1,
            event_type="run_created",
            run=run,
            occurred_at=run.created_at,
        )
        try:
            with self._connection() as connection:
                _ = connection.execute("BEGIN IMMEDIATE")
                _ = connection.execute(
                    """
                    INSERT INTO agent_runs(
                        run_id, tenant_id, request_sha256, run_json, revision,
                        head_step_sha256, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.tenant_id,
                        create_sha256,
                        run_json,
                        run.revision,
                        run.head_step_sha256,
                        run.state,
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                    ),
                )
                _ = connection.execute(
                    """
                    INSERT INTO agent_run_events(
                        run_id, sequence, event_sha256, event_json, occurred_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        event.sequence,
                        contract_sha256(event),
                        event.model_dump_json(),
                        event.occurred_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            current = self.get(run.tenant_id, run.run_id)
            if current is not None and self._request_sha256(run.run_id) == create_sha256:
                return current
            raise AgentRunConflictError("agent_run_idempotency_conflict") from error
        return run

    def get(self, tenant_id: str, run_id: str) -> AgentRun | None:
        projection = self._projection(tenant_id, run_id)
        if projection is None:
            return None
        rebuilt = self.rebuild(tenant_id, run_id)
        if projection != rebuilt:
            raise AgentRunConflictError("agent_run_projection_mismatch")
        return rebuilt

    def _projection(self, tenant_id: str, run_id: str) -> AgentRun | None:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT run_json FROM agent_runs WHERE tenant_id = ? AND run_id = ?",
                    (tenant_id, run_id),
                ).fetchone(),
            )
        return (
            None if row is None else AgentRun.model_validate_json(_STRING.validate_python(row[0]))
        )

    def rebuild(self, tenant_id: str, run_id: str) -> AgentRun:
        with self._connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT event_json FROM agent_run_events
                    WHERE run_id = ? ORDER BY sequence
                    """,
                    (run_id,),
                ).fetchall(),
            )
        events = tuple(
            AgentRunEvent.model_validate_json(_STRING.validate_python(row[0])) for row in rows
        )
        if not events:
            raise AgentRunConflictError("agent_run_event_history_missing")
        if any(event.run.tenant_id != tenant_id for event in events):
            raise AgentRunConflictError("agent_run_tenant_history_invalid")
        previous_sha256: str | None = None
        for sequence, event in enumerate(events, start=1):
            if event.sequence != sequence or event.previous_event_sha256 != previous_sha256:
                raise AgentRunConflictError("agent_run_event_history_invalid")
            previous_sha256 = contract_sha256(event)
        return events[-1].run

    def list_runs(self, tenant_id: str, *, limit: int = 50) -> tuple[AgentRun, ...]:
        if limit < 1 or limit > _MAX_LIST_LIMIT:
            raise ValueError("agent_run_list_limit_invalid")
        with self._connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT run_id FROM agent_runs
                    WHERE tenant_id = ?
                    ORDER BY updated_at DESC, run_id
                    LIMIT ?
                    """,
                    (tenant_id, limit),
                ).fetchall(),
            )
        runs: list[AgentRun] = []
        for row in rows:
            run_id = _STRING.validate_python(row[0])
            run = self.get(tenant_id, run_id)
            if run is None:
                raise AgentRunConflictError("agent_run_projection_missing")
            runs.append(run)
        return tuple(runs)

    def steps(self, tenant_id: str, run_id: str) -> tuple[AgentStep, ...]:
        with self._connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT step.step_json FROM agent_steps AS step
                    JOIN agent_runs AS run ON run.run_id = step.run_id
                    WHERE run.tenant_id = ? AND step.run_id = ? ORDER BY step.sequence
                    """,
                    (tenant_id, run_id),
                ).fetchall(),
            )
        return tuple(AgentStep.model_validate_json(_STRING.validate_python(row[0])) for row in rows)

    def records(self, tenant_id: str, run_id: str) -> tuple[AgentRecord, ...]:
        with self._connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT record.record_json FROM agent_records AS record
                    JOIN agent_runs AS run ON run.run_id = record.run_id
                    WHERE run.tenant_id = ? AND record.run_id = ? ORDER BY record.rowid
                    """,
                    (tenant_id, run_id),
                ).fetchall(),
            )
        return tuple(
            AgentRecord.model_validate_json(_STRING.validate_python(row[0])) for row in rows
        )

    def append_step(  # noqa: PLR0913 - append-only CAS boundary keeps authority explicit.
        self,
        run: AgentRun,
        step: AgentStep,
        *,
        state: AgentRunState,
        expected_revision: int,
        records: tuple[AgentRecord, ...] = (),
        blocked_reason: str | None = None,
    ) -> AgentRun:
        if step.run_id != run.run_id:
            raise AgentRunConflictError("agent_step_run_conflict")
        if step.sequence != expected_revision:
            raise AgentRunConflictError("agent_step_sequence_conflict")
        if step.parent_step_sha256 != run.head_step_sha256:
            raise AgentRunConflictError("agent_step_parent_conflict")
        if run.revision != expected_revision:
            raise AgentRunConflictError("agent_run_revision_conflict")
        if any(record.run_id != run.run_id for record in records):
            raise AgentRunConflictError("agent_record_run_conflict")
        step_sha256 = contract_sha256(step)
        updated = run.model_copy(
            update={
                "state": state,
                "revision": run.revision + 1,
                "head_step_sha256": step_sha256,
                "updated_at": step.occurred_at,
                "blocked_reason": blocked_reason,
            }
        )
        previous_event_sha256 = self._latest_event_sha256(run.run_id)
        event = AgentRunEvent(
            schema_version="trace.agent-run-event.v1",
            run_id=run.run_id,
            sequence=updated.revision,
            event_type="step_appended",
            previous_event_sha256=previous_event_sha256,
            run=updated,
            step_sha256=step_sha256,
            occurred_at=step.occurred_at,
        )
        try:
            with self._connection() as connection:
                _ = connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE agent_runs
                    SET run_json = ?, revision = ?, head_step_sha256 = ?, state = ?, updated_at = ?
                    WHERE run_id = ? AND revision = ? AND head_step_sha256 IS ?
                    """,
                    (
                        updated.model_dump_json(),
                        updated.revision,
                        updated.head_step_sha256,
                        updated.state,
                        updated.updated_at.isoformat(),
                        run.run_id,
                        expected_revision,
                        run.head_step_sha256,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentRunConflictError("agent_run_revision_conflict")
                for record in records:
                    _ = connection.execute(
                        """
                        INSERT INTO agent_records(
                            record_id, run_id, kind, record_sha256, record_json, occurred_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.record_id,
                            record.run_id,
                            record.kind,
                            contract_sha256(record),
                            record.model_dump_json(),
                            record.occurred_at.isoformat(),
                        ),
                    )
                _ = connection.execute(
                    """
                    INSERT INTO agent_run_events(
                        run_id, sequence, event_sha256, event_json, occurred_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        event.sequence,
                        contract_sha256(event),
                        event.model_dump_json(),
                        event.occurred_at.isoformat(),
                    ),
                )
                _ = connection.execute(
                    """
                    INSERT INTO agent_steps(
                        step_id, run_id, sequence, step_sha256, step_json, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step.step_id,
                        step.run_id,
                        step.sequence,
                        step_sha256,
                        step.model_dump_json(),
                        step.occurred_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AgentRunConflictError("agent_step_append_conflict") from error
        return updated

    def claim_tool_idempotency(
        self,
        *,
        tenant_id: str,
        run_id: str,
        idempotency_key: str,
        invocation_sha256: str,
        claimed_at: str,
    ) -> None:
        """Claim a tenant-visible effect identity before any execution can start."""
        try:
            with self._connection() as connection:
                _ = connection.execute(
                    """
                    INSERT INTO agent_tool_idempotency(
                        tenant_id, idempotency_key, run_id, invocation_sha256, claimed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (tenant_id, idempotency_key, run_id, invocation_sha256, claimed_at),
                )
        except sqlite3.IntegrityError as error:
            with self._connection() as connection:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """
                        SELECT run_id, invocation_sha256 FROM agent_tool_idempotency
                        WHERE tenant_id = ? AND idempotency_key = ?
                        """,
                        (tenant_id, idempotency_key),
                    ).fetchone(),
                )
            if row is not None and (
                _STRING.validate_python(row[0]) == run_id
                and _STRING.validate_python(row[1]) == invocation_sha256
            ):
                return
            raise AgentRunConflictError("tool_idempotency_conflict") from error

    def _request_sha256(self, run_id: str) -> str | None:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT request_sha256 FROM agent_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone(),
            )
        return None if row is None else _STRING.validate_python(row[0])

    def _latest_event_sha256(self, run_id: str) -> str:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT event_sha256 FROM agent_run_events
                    WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (run_id,),
                ).fetchone(),
            )
        if row is None:
            raise AgentRunConflictError("agent_run_event_history_missing")
        return _STRING.validate_python(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        _ = connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()


__all__ = ["AgentRunConflictError", "SqliteAgentRunRepository"]
