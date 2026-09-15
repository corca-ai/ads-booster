from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_schedule import (
    AgentSchedule,
    ScheduleOccurrence,
    ScheduleOccurrenceState,
    ScheduleStatus,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ads_booster.agent.service.sqlite_repository import RepositoryAdmission


class ScheduleConflictError(ValueError):
    pass


class ScheduleNotFoundError(ValueError):
    pass


_OPTIONAL_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_COUNT_ROWS: TypeAdapter[list[tuple[str, int]]] = TypeAdapter(list[tuple[str, int]])
_COUNT: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])


@dataclass(frozen=True, slots=True)
class ScheduleRepository:
    database_path: Path

    def __post_init__(self) -> None:
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as database:
            _ = database.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS agent_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_schedules_tenant_status
                    ON agent_schedules(tenant_id, status, updated_at);
                CREATE TABLE IF NOT EXISTS agent_schedule_revisions (
                    schedule_id TEXT NOT NULL REFERENCES agent_schedules(schedule_id) ON DELETE RESTRICT,
                    revision INTEGER NOT NULL,
                    schedule_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id, revision)
                );
                CREATE TABLE IF NOT EXISTS agent_schedule_occurrences (
                    occurrence_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES agent_schedules(schedule_id) ON DELETE RESTRICT,
                    schedule_revision INTEGER NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    occurrence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_for)
                );
                CREATE INDEX IF NOT EXISTS agent_schedule_occurrences_state
                    ON agent_schedule_occurrences(schedule_id, state, scheduled_for);
                CREATE TRIGGER IF NOT EXISTS agent_schedule_revisions_immutable
                BEFORE UPDATE ON agent_schedule_revisions BEGIN
                    SELECT RAISE(ABORT, 'schedule revisions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS agent_schedule_revisions_append_only
                BEFORE DELETE ON agent_schedule_revisions BEGIN
                    SELECT RAISE(ABORT, 'schedule revisions are append-only');
                END;
                """
            )
        self.database_path.chmod(0o600)

    def create(self, schedule: AgentSchedule) -> AgentSchedule:
        try:
            with self.connect() as database:
                _ = database.execute("BEGIN IMMEDIATE")
                _ = database.execute(
                    """INSERT INTO agent_schedules(
                    schedule_id, tenant_id, owner_id, revision, status, schedule_json,
                    created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        schedule.schedule_id,
                        schedule.tenant_id,
                        schedule.authority.member_id,
                        schedule.revision,
                        schedule.status,
                        schedule.model_dump_json(),
                        schedule.created_at.isoformat(),
                        schedule.updated_at.isoformat(),
                    ),
                )
                self._append_revision(database, schedule)
        except sqlite3.IntegrityError as error:
            current = self.get(schedule.tenant_id, schedule.schedule_id)
            if current is not None and current == schedule:
                return current
            raise ScheduleConflictError("schedule_id_conflict") from error
        return schedule

    def replace(
        self, schedule: AgentSchedule, *, expected_revision: int, actor_id: str
    ) -> AgentSchedule:
        if schedule.revision != expected_revision + 1:
            raise ScheduleConflictError("schedule_revision_sequence_invalid")
        with self.connect() as database:
            _ = database.execute("BEGIN IMMEDIATE")
            current = self._required(database, schedule.tenant_id, schedule.schedule_id)
            self._authorize(current, actor_id)
            if current.revision != expected_revision:
                raise ScheduleConflictError("schedule_revision_conflict")
            if (
                schedule.created_at != current.created_at
                or schedule.authority.workspace_id != current.authority.workspace_id
                or schedule.authority.member_id != current.authority.member_id
            ):
                raise ScheduleConflictError("schedule_immutable_identity_changed")
            changed = database.execute(
                """UPDATE agent_schedules SET revision=?, status=?, schedule_json=?, updated_at=?
                WHERE schedule_id=? AND tenant_id=? AND revision=?""",
                (
                    schedule.revision,
                    schedule.status,
                    schedule.model_dump_json(),
                    schedule.updated_at.isoformat(),
                    schedule.schedule_id,
                    schedule.tenant_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise ScheduleConflictError("schedule_revision_conflict")
            self._append_revision(database, schedule)
        return schedule

    def get(self, tenant_id: str, schedule_id: str) -> AgentSchedule | None:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    "SELECT schedule_json FROM agent_schedules WHERE tenant_id=? AND schedule_id=?",
                    (tenant_id, schedule_id),
                ).fetchone()
            )
        if row is None:
            return None
        return AgentSchedule.model_validate_json(row[0])

    def get_for_occurrence(self, occurrence: ScheduleOccurrence) -> AgentSchedule:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT schedule_json FROM agent_schedule_revisions
                    WHERE schedule_id=? AND revision=?""",
                    (occurrence.schedule_id, occurrence.schedule_revision),
                ).fetchone()
            )
        if row is None:
            raise ScheduleNotFoundError("schedule_revision_not_found")
        return AgentSchedule.model_validate_json(row[0])

    def list_for_tenant(
        self,
        tenant_id: str,
        *,
        owner_id: str | None = None,
        status: ScheduleStatus | None = None,
        limit: int = 100,
    ) -> tuple[AgentSchedule, ...]:
        if limit < 1 or limit > 200:
            raise ScheduleConflictError("schedule_list_limit_invalid")
        clauses = ["tenant_id=?"]
        values: list[str | int] = [tenant_id]
        if owner_id is not None:
            clauses.append("owner_id=?")
            values.append(owner_id)
        if status is not None:
            clauses.append("status=?")
            values.append(status)
        values.append(limit)
        with self.connect() as database:
            query = (
                f"SELECT schedule_json FROM agent_schedules WHERE {' AND '.join(clauses)} "
                f"ORDER BY created_at, schedule_id LIMIT ?"
            )
            rows = _ROWS.validate_python(database.execute(query, values).fetchall())
        return tuple(AgentSchedule.model_validate_json(row[0]) for row in rows)

    def list_active(self, *, limit: int = 200) -> tuple[AgentSchedule, ...]:
        if limit < 1 or limit > 1000:
            raise ScheduleConflictError("schedule_list_limit_invalid")
        with self.connect() as database:
            rows = _ROWS.validate_python(
                database.execute(
                    """SELECT schedule_json FROM agent_schedules WHERE status=?
                    ORDER BY updated_at, schedule_id LIMIT ?""",
                    (ScheduleStatus.ACTIVE, limit),
                ).fetchall()
            )
        return tuple(AgentSchedule.model_validate_json(row[0]) for row in rows)

    def reserve(self, occurrence: ScheduleOccurrence) -> ScheduleOccurrence:
        try:
            with self.connect() as database:
                _ = database.execute("BEGIN IMMEDIATE")
                schedule = self._required(database, "", occurrence.schedule_id, tenant_optional=True)
                if schedule.revision != occurrence.schedule_revision:
                    raise ScheduleConflictError("occurrence_schedule_revision_stale")
                if schedule.status is not ScheduleStatus.ACTIVE:
                    raise ScheduleConflictError("occurrence_schedule_inactive")
                _ = database.execute(
                    """INSERT INTO agent_schedule_occurrences(
                    occurrence_id, schedule_id, schedule_revision, scheduled_for, run_id,
                    state, occurrence_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        occurrence.occurrence_id,
                        occurrence.schedule_id,
                        occurrence.schedule_revision,
                        occurrence.scheduled_for.isoformat(),
                        occurrence.run_id,
                        occurrence.state,
                        occurrence.model_dump_json(),
                        occurrence.created_at.isoformat(),
                        occurrence.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            current = self.occurrence_for(
                occurrence.schedule_id, scheduled_for=occurrence.scheduled_for
            )
            if current is not None:
                return current
            raise ScheduleConflictError("occurrence_identity_conflict") from error
        return occurrence

    def occurrence(self, occurrence_id: str) -> ScheduleOccurrence | None:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    "SELECT occurrence_json FROM agent_schedule_occurrences WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
            )
        if row is None:
            return None
        return ScheduleOccurrence.model_validate_json(row[0])

    def occurrence_by_run(self, run_id: str) -> ScheduleOccurrence | None:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE run_id=?""",
                    (run_id,),
                ).fetchone()
            )
        if row is None:
            return None
        return ScheduleOccurrence.model_validate_json(row[0])

    def occurrence_for(
        self, schedule_id: str, *, scheduled_for: datetime
    ) -> ScheduleOccurrence | None:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE schedule_id=? AND scheduled_for=?""",
                    (schedule_id, scheduled_for.isoformat()),
                ).fetchone()
            )
        if row is None:
            return None
        return ScheduleOccurrence.model_validate_json(row[0])

    def transition(
        self,
        occurrence: ScheduleOccurrence,
        *,
        expected_state: ScheduleOccurrenceState,
    ) -> ScheduleOccurrence:
        with self.connect() as database:
            _ = database.execute("BEGIN IMMEDIATE")
            changed = database.execute(
                """UPDATE agent_schedule_occurrences SET state=?, occurrence_json=?, updated_at=?
                WHERE occurrence_id=? AND state=?""",
                (
                    occurrence.state,
                    occurrence.model_dump_json(),
                    occurrence.updated_at.isoformat(),
                    occurrence.occurrence_id,
                    expected_state,
                ),
            ).rowcount
            if changed != 1:
                raise ScheduleConflictError("occurrence_state_conflict")
        return occurrence

    def active_occurrences(self, schedule_id: str) -> tuple[ScheduleOccurrence, ...]:
        terminal = (
            ScheduleOccurrenceState.SUCCEEDED,
            ScheduleOccurrenceState.BLOCKED,
            ScheduleOccurrenceState.FAILED,
            ScheduleOccurrenceState.SKIPPED_OVERLAP,
            ScheduleOccurrenceState.SKIPPED_MISFIRE,
        )
        placeholders = ",".join("?" for _ in terminal)
        with self.connect() as database:
            rows = _ROWS.validate_python(
                database.execute(
                    f"""SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE schedule_id=? AND state NOT IN ({placeholders}) ORDER BY scheduled_for""",
                    (schedule_id, *(state.value for state in terminal)),
                ).fetchall()
            )
        return tuple(ScheduleOccurrence.model_validate_json(row[0]) for row in rows)

    def latest_occurrence(self, schedule_id: str) -> ScheduleOccurrence | None:
        with self.connect() as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE schedule_id=? ORDER BY scheduled_for DESC LIMIT 1""",
                    (schedule_id,),
                ).fetchone()
            )
        if row is None:
            return None
        return ScheduleOccurrence.model_validate_json(row[0])

    def occurrence_count(self, schedule_id: str) -> int:
        with self.connect() as database:
            row = _COUNT.validate_python(
                database.execute(
                    "SELECT COUNT(*) FROM agent_schedule_occurrences WHERE schedule_id=?",
                    (schedule_id,),
                ).fetchone()
            )
        return row[0]

    def occurrences(
        self, schedule_id: str, *, limit: int = 100
    ) -> tuple[ScheduleOccurrence, ...]:
        if limit < 1 or limit > 1000:
            raise ScheduleConflictError("schedule_occurrence_limit_invalid")
        with self.connect() as database:
            rows = _ROWS.validate_python(
                database.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE schedule_id=? ORDER BY scheduled_for DESC LIMIT ?""",
                    (schedule_id, limit),
                ).fetchall()
            )
        return tuple(ScheduleOccurrence.model_validate_json(row[0]) for row in rows)

    def reserved_occurrences(self, *, limit: int = 200) -> tuple[ScheduleOccurrence, ...]:
        if limit < 1 or limit > 1000:
            raise ScheduleConflictError("schedule_list_limit_invalid")
        with self.connect() as database:
            rows = _ROWS.validate_python(
                database.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE state=? ORDER BY scheduled_for, occurrence_id LIMIT ?""",
                    (ScheduleOccurrenceState.RESERVED, limit),
                ).fetchall()
            )
        return tuple(ScheduleOccurrence.model_validate_json(row[0]) for row in rows)

    def health_counts(self) -> dict[str, int]:
        with self.connect() as database:
            active = _COUNT.validate_python(
                database.execute(
                    "SELECT COUNT(*) FROM agent_schedules WHERE status=?",
                    (ScheduleStatus.ACTIVE,),
                ).fetchone()
            )[0]
            rows = _COUNT_ROWS.validate_python(
                database.execute(
                    """SELECT state,COUNT(*) FROM agent_schedule_occurrences
                    GROUP BY state"""
                ).fetchall()
            )
        return {"active_schedules": active, **{state: count for state, count in rows}}

    def admission_for(self, occurrence_id: str, *, updated_at: datetime) -> RepositoryAdmission:
        def admission(connection: sqlite3.Connection) -> None:
            row = _OPTIONAL_ROW.validate_python(
                connection.execute(
                    """SELECT occurrence_json FROM agent_schedule_occurrences
                    WHERE occurrence_id=? AND state=?""",
                    (occurrence_id, ScheduleOccurrenceState.RESERVED),
                ).fetchone()
            )
            if row is None:
                raise ScheduleConflictError("occurrence_admission_conflict")
            current = ScheduleOccurrence.model_validate_json(row[0])
            admitted = current.model_copy(
                update={"state": ScheduleOccurrenceState.ADMITTED, "updated_at": updated_at}
            )
            _ = connection.execute(
                """UPDATE agent_schedule_occurrences SET state=?, occurrence_json=?, updated_at=?
                WHERE occurrence_id=? AND state=?""",
                (
                    admitted.state,
                    admitted.model_dump_json(),
                    admitted.updated_at.isoformat(),
                    occurrence_id,
                    ScheduleOccurrenceState.RESERVED,
                ),
            )

        return admission

    def _required(
        self,
        database: sqlite3.Connection,
        tenant_id: str,
        schedule_id: str,
        *,
        tenant_optional: bool = False,
    ) -> AgentSchedule:
        query = "SELECT schedule_json FROM agent_schedules WHERE schedule_id=?"
        values: tuple[str, ...] = (schedule_id,)
        if not tenant_optional:
            query += " AND tenant_id=?"
            values = (schedule_id, tenant_id)
        row = _OPTIONAL_ROW.validate_python(database.execute(query, values).fetchone())
        if row is None:
            raise ScheduleNotFoundError("schedule_not_found")
        return AgentSchedule.model_validate_json(row[0])

    @staticmethod
    def _authorize(schedule: AgentSchedule, actor_id: str) -> None:
        if schedule.authority.member_id != actor_id:
            raise ScheduleConflictError("schedule_owner_required")

    @staticmethod
    def _append_revision(database: sqlite3.Connection, schedule: AgentSchedule) -> None:
        _ = database.execute(
            """INSERT INTO agent_schedule_revisions(
            schedule_id, revision, schedule_json, recorded_at
            ) VALUES (?, ?, ?, ?)""",
            (
                schedule.schedule_id,
                schedule.revision,
                schedule.model_dump_json(),
                schedule.updated_at.isoformat(),
            ),
        )

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = [
    "ScheduleConflictError",
    "ScheduleNotFoundError",
    "ScheduleRepository",
]
