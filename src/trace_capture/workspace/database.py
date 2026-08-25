from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator

_DATABASE_FILENAME: Final = "workspace.sqlite3"
_SCHEMA: Final = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    code_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    code_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, member_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS contexts (
    workspace_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, context_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assets (
    workspace_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    context_id TEXT,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, asset_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, context_id) REFERENCES contexts(workspace_id, context_id)
);
CREATE TABLE IF NOT EXISTS private_sessions (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    history_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, member_id, session_id),
    FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, member_id)
        ON DELETE CASCADE
);
"""


class WorkspaceDatabase:
    path: Path

    def __init__(self, home: Path) -> None:
        """Initialize a private SQLite database inside the configured agent home."""
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / _DATABASE_FILENAME
        with self.connect() as connection:
            _ = connection.executescript(_SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, write: bool = False) -> Generator[sqlite3.Connection]:
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


@dataclass(frozen=True, slots=True)
class WorkspaceRepositoryBase:
    _database: WorkspaceDatabase


type SqliteValue = bytes | float | int | str | None
type SqliteRow = tuple[SqliteValue, ...]


class SqliteCursor(Protocol):
    def fetchone(self) -> SqliteRow | None: ...

    def fetchall(self) -> list[SqliteRow]: ...


def default_agent_home() -> Path:
    configured = os.environ.get("TRACE_AGENT_HOME")
    return Path(configured).expanduser() if configured is not None else Path.home() / ".trace-agent"
