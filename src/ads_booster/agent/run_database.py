from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_FILENAME: Final = "agent-runs.sqlite3"
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'queued', 'running', 'awaiting_approval', 'awaiting_input',
        'completed', 'failed', 'blocked', 'cancelled'
    )),
    revision INTEGER NOT NULL,
    run_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_runs_connector_state
ON agent_runs (connector_id, state, updated_at);
"""


class AgentRunDatabase:
    path: Path

    def __init__(self, home: Path) -> None:
        """Create the private Agent run database."""
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / _FILENAME
        with self.connect(write=True) as connection:
            _ = connection.executescript(_SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, write: bool = False) -> Generator[sqlite3.Connection]:
        """Open one bounded SQLite transaction."""
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
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
