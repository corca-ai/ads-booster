from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.agent.run_database import AgentRunDatabase
from ads_booster.agent.run_models import (
    AgentObservation,
    AgentRun,
    AgentRunId,
    AgentRunState,
    AgentRunUpdate,
    ObservationKind,
)

if TYPE_CHECKING:
    from pathlib import Path


_ALLOWED_TRANSITIONS: Final[dict[AgentRunState, frozenset[AgentRunState]]] = {
    AgentRunState.QUEUED: frozenset((AgentRunState.RUNNING, AgentRunState.CANCELLED)),
    AgentRunState.RUNNING: frozenset(
        (
            AgentRunState.QUEUED,
            AgentRunState.RUNNING,
            AgentRunState.AWAITING_APPROVAL,
            AgentRunState.AWAITING_INPUT,
            AgentRunState.COMPLETED,
            AgentRunState.FAILED,
            AgentRunState.BLOCKED,
            AgentRunState.CANCELLED,
        )
    ),
    AgentRunState.AWAITING_APPROVAL: frozenset(
        (
            AgentRunState.QUEUED,
            AgentRunState.RUNNING,
            AgentRunState.COMPLETED,
            AgentRunState.CANCELLED,
        )
    ),
    AgentRunState.AWAITING_INPUT: frozenset(
        (
            AgentRunState.QUEUED,
            AgentRunState.RUNNING,
            AgentRunState.BLOCKED,
            AgentRunState.CANCELLED,
        )
    ),
    AgentRunState.COMPLETED: frozenset(),
    AgentRunState.FAILED: frozenset(),
    AgentRunState.BLOCKED: frozenset(),
    AgentRunState.CANCELLED: frozenset(),
}
_TERMINAL_STATES: Final = frozenset(
    (
        AgentRunState.COMPLETED,
        AgentRunState.FAILED,
        AgentRunState.BLOCKED,
        AgentRunState.CANCELLED,
    )
)
_RUN_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_RUN_ROWS: TypeAdapter[tuple[tuple[str], ...]] = TypeAdapter(tuple[tuple[str], ...])


class AgentRunAlreadyExistsError(RuntimeError):
    run_id: AgentRunId

    def __init__(self, run_id: AgentRunId) -> None:
        """Create a duplicate run admission failure."""
        self.run_id = run_id
        super().__init__(run_id)


class AgentRunNotFoundError(RuntimeError):
    run_id: AgentRunId

    def __init__(self, run_id: AgentRunId) -> None:
        """Create a missing run lookup failure."""
        self.run_id = run_id
        super().__init__(run_id)


class AgentRunRevisionError(RuntimeError):
    run_id: AgentRunId
    expected_revision: int

    def __init__(self, run_id: AgentRunId, expected_revision: int) -> None:
        """Create an optimistic revision conflict."""
        self.run_id = run_id
        self.expected_revision = expected_revision
        super().__init__(run_id, expected_revision)


class AgentRunTransitionError(RuntimeError):
    run_id: AgentRunId
    current: AgentRunState
    requested: AgentRunState

    def __init__(
        self,
        run_id: AgentRunId,
        current: AgentRunState,
        requested: AgentRunState,
    ) -> None:
        """Create an invalid lifecycle transition failure."""
        self.run_id = run_id
        self.current = current
        self.requested = requested
        super().__init__(run_id, current, requested)


class AgentObservationSequenceError(RuntimeError):
    run_id: AgentRunId
    expected: int
    actual: int

    def __init__(self, run_id: AgentRunId, expected: int, actual: int) -> None:
        """Create an out-of-order observation failure."""
        self.run_id = run_id
        self.expected = expected
        self.actual = actual
        super().__init__(run_id, expected, actual)


class AgentRunTerminalReasonError(RuntimeError):
    run_id: AgentRunId
    state: AgentRunState

    def __init__(self, run_id: AgentRunId, state: AgentRunState) -> None:
        """Create a missing or misplaced terminal reason failure."""
        self.run_id = run_id
        self.state = state
        super().__init__(run_id, state)


class AgentRunStore:
    _database: AgentRunDatabase

    def __init__(self, home: Path) -> None:
        """Open the durable Agent run store."""
        self._database = AgentRunDatabase(home)

    @property
    def database_path(self) -> Path:
        """Return the private SQLite path."""
        return self._database.path

    def create(self, run: AgentRun, *, now: float) -> AgentRun:
        """Admit a new queued run without replacing an existing goal."""
        created = run.model_copy(update={"created_at": now, "updated_at": now})
        try:
            with self._database.connect(write=True) as connection:
                _ = connection.execute(
                    "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        created.run_id,
                        created.connector_id,
                        created.state,
                        created.revision,
                        created.model_dump_json(),
                        created.created_at,
                        created.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AgentRunAlreadyExistsError(created.run_id) from error
        return created

    def get(self, run_id: AgentRunId) -> AgentRun:
        """Load one run by its durable identity."""
        with self._database.connect() as connection:
            row = _RUN_ROW.validate_python(
                connection.execute(
                    "SELECT run_json FROM agent_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            )
        return self._parse_row(run_id, row)

    def recover_interrupted(self, *, at: float) -> int:
        with self._database.connect() as connection:
            rows = _RUN_ROWS.validate_python(
                connection.execute(
                    "SELECT run_json FROM agent_runs WHERE state = ? ORDER BY created_at",
                    (AgentRunState.RUNNING,),
                ).fetchall()
            )
        running = tuple(AgentRun.model_validate_json(row[0]) for row in rows)
        for current in running:
            _ = self.update(
                current.run_id,
                AgentRunUpdate(
                    expected_revision=current.revision,
                    state=AgentRunState.QUEUED,
                    at=at,
                    observation=AgentObservation(
                        sequence=len(current.observations) + 1,
                        kind=ObservationKind.FAILURE,
                        summary="workspace service restarted before the Agent run completed",
                        data={"reason": "service_restart"},
                    ),
                ),
            )
        return len(running)

    def update(self, run_id: AgentRunId, update: AgentRunUpdate) -> AgentRun:
        """Commit one validated state, history, and observation revision."""
        with self._database.connect(write=True) as connection:
            row = _RUN_ROW.validate_python(
                connection.execute(
                    "SELECT run_json FROM agent_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            )
            current = self._parse_row(run_id, row)
            if current.revision != update.expected_revision:
                raise AgentRunRevisionError(run_id, update.expected_revision)
            if update.state not in _ALLOWED_TRANSITIONS[current.state]:
                raise AgentRunTransitionError(run_id, current.state, update.state)
            self._require_terminal_reason(run_id, update)
            observations = current.observations
            if update.observation is not None:
                expected_sequence = len(observations) + 1
                if update.observation.sequence != expected_sequence:
                    raise AgentObservationSequenceError(
                        run_id,
                        expected_sequence,
                        update.observation.sequence,
                    )
                observations = (*observations, update.observation)
            updated = current.model_copy(
                update={
                    "state": update.state,
                    "revision": current.revision + 1,
                    "history": current.history if update.history is None else update.history,
                    "observations": observations,
                    "terminal_reason": update.terminal_reason,
                    "updated_at": update.at,
                }
            )
            result = connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, revision = ?, run_json = ?, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    updated.state,
                    updated.revision,
                    updated.model_dump_json(),
                    updated.updated_at,
                    run_id,
                    current.revision,
                ),
            )
            if result.rowcount != 1:
                raise AgentRunRevisionError(run_id, update.expected_revision)
        return updated

    def _parse_row(self, run_id: AgentRunId, row: tuple[str] | None) -> AgentRun:
        if row is None:
            raise AgentRunNotFoundError(run_id)
        return AgentRun.model_validate_json(row[0])

    def _require_terminal_reason(self, run_id: AgentRunId, update: AgentRunUpdate) -> None:
        has_reason = update.terminal_reason is not None
        if (update.state in _TERMINAL_STATES) != has_reason:
            raise AgentRunTerminalReasonError(run_id, update.state)
