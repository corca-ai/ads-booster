from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path


def initialize_threads_effect_fence(database: sqlite3.Connection) -> None:
    _ = database.execute(
        """CREATE TABLE IF NOT EXISTS threads_connection_tombstones (
        connection_id TEXT PRIMARY KEY,
        deleted_at TEXT NOT NULL
        )"""
    )


def threads_connection_deleted(database: sqlite3.Connection, connection_id: str) -> bool:
    return (
        database.execute(
            "SELECT 1 FROM threads_connection_tombstones WHERE connection_id=?",
            (connection_id,),
        ).fetchone()
        is not None
    )


@dataclass(frozen=True, slots=True)
class ThreadsEffectFence:
    database_path: Path
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    @contextmanager
    def hold(self) -> Generator[None]:
        with self._lock:
            yield

    def block(
        self,
        database: sqlite3.Connection,
        connection_ids: frozenset[str],
        *,
        now: datetime,
    ) -> None:
        initialize_threads_effect_fence(database)
        _ = database.executemany(
            """INSERT INTO threads_connection_tombstones(connection_id,deleted_at)
            VALUES(?,?) ON CONFLICT(connection_id) DO UPDATE SET deleted_at=excluded.deleted_at""",
            ((connection_id, now.isoformat()) for connection_id in sorted(connection_ids)),
        )

    def allow(self, connection_id: str) -> None:
        with self.hold(), closing(sqlite3.connect(self.database_path)) as database, database:
            initialize_threads_effect_fence(database)
            _ = database.execute(
                "DELETE FROM threads_connection_tombstones WHERE connection_id=?",
                (connection_id,),
            )


__all__ = [
    "ThreadsEffectFence",
    "initialize_threads_effect_fence",
    "threads_connection_deleted",
]
