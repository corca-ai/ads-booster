from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_FILENAME: Final = "automation.sqlite3"
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS automation_queue (
    queue_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('submitted', 'claimed', 'running', 'review', 'accepted', 'rejected', 'failed')),
    due_at REAL NOT NULL,
    attempts INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    worker_id TEXT,
    lease_until REAL,
    run_id TEXT,
    run_idempotency_key TEXT,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    failure_code TEXT,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (workspace_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS automation_single_active
ON automation_queue ((1)) WHERE state IN ('claimed', 'running');
CREATE INDEX IF NOT EXISTS automation_due
ON automation_queue (state, due_at, created_at);
CREATE TABLE IF NOT EXISTS generation_campaigns (
    campaign_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'stopped', 'completed')),
    next_variation INTEGER NOT NULL,
    current_queue_id TEXT,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS generation_campaigns_workspace
ON generation_campaigns (workspace_id, created_at);
CREATE INDEX IF NOT EXISTS generation_campaigns_active
ON generation_campaigns (state, created_at);
"""


class AutomationDatabase:
    path: Path

    def __init__(self, home: Path) -> None:
        """Create the private automation database and its schema."""
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / _FILENAME
        with self.connect(write=True) as connection:
            _ = connection.executescript(_SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, write: bool = False) -> Generator[sqlite3.Connection]:
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
