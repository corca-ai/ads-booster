"""Durable lookup of the latest scoped memory-selection receipt."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope, MemorySelectionReceipt
from ads_booster.learning.memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 9, 7, tzinfo=UTC)
_BINDING_ROW: TypeAdapter[tuple[str, str, str, str]] = TypeAdapter(tuple[str, str, str, str])
_SELECTION_ROW: TypeAdapter[tuple[str, str]] = TypeAdapter(tuple[str, str])


def access(*, workspace: str = "trace", actor: str = "reviewer") -> MemoryAccess:
    return MemoryAccess(
        scope=MemoryScope(workspace_id=workspace, product_id="trace"),
        actor_id=actor,
        can_review=True,
    )


def select(
    store: SQLiteMemoryStore,
    actor: MemoryAccess,
    *,
    run_id: str = "run-1",
    now: datetime = NOW,
) -> None:
    _ = store.select(actor, query="calendar", run_id=run_id, now=now)


def latest_receipt_id(
    store: SQLiteMemoryStore,
    actor: MemoryAccess,
    *,
    run_id: str = "run-1",
) -> str | None:
    selection = store.latest_selection(actor, run_id=run_id, now=NOW)
    return None if selection is None else selection.receipt.selection_id


def test_selection_binding_columns_migrate_a_legacy_database(tmp_path: Path) -> None:
    # Given
    source = SQLiteMemoryStore(tmp_path / "source.sqlite")
    actor = access()
    select(
        source,
        actor,
        now=datetime(2026, 9, 7, 0, 30, tzinfo=timezone(timedelta(hours=9))),
    )
    with source.connect() as db:
        receipt = _SELECTION_ROW.validate_python(
            db.execute("SELECT selection_id, data_json FROM agent_memory_selections").fetchone()
        )
    legacy_path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(legacy_path)) as db:
        _ = db.execute(
            """
            CREATE TABLE agent_memory_selections (
                selection_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL
            )
            """
        )
        _ = db.execute("INSERT INTO agent_memory_selections VALUES (?, ?)", receipt)
        _ = db.execute("INSERT INTO agent_memory_selections VALUES (?, ?)", ("invalid", "not-json"))
        db.commit()

    # When
    migrated = SQLiteMemoryStore(legacy_path)
    with migrated.connect() as db:
        binding = _BINDING_ROW.validate_python(
            db.execute(
                """
                SELECT run_id, workspace_id, actor_id, selected_at
                FROM agent_memory_selections
                WHERE selection_id=?
                """,
                (receipt[0],),
            ).fetchone()
        )
        _ = db.execute(
            """
            CREATE TRIGGER no_selection_rebackfill
            BEFORE UPDATE ON agent_memory_selections
            BEGIN SELECT RAISE(ABORT, 'selection_rebackfill'); END
            """
        )

    # Then
    assert binding == ("run-1", "trace", "reviewer", "2026-09-06T15:30:00+00:00")
    _ = SQLiteMemoryStore(legacy_path)


def test_migration_excludes_a_legacy_receipt_without_actor_binding(tmp_path: Path) -> None:
    # Given
    source = SQLiteMemoryStore(tmp_path / "source.sqlite")
    actor = access()
    select(source, actor)
    with source.connect() as db:
        raw = _SELECTION_ROW.validate_python(
            db.execute("SELECT selection_id, data_json FROM agent_memory_selections").fetchone()
        )[1]
    legacy_receipt = MemorySelectionReceipt.model_validate_json(raw).model_copy(
        update={"selection_id": "legacy-unbound", "actor_id": None, "selection_sha256": None}
    )
    legacy_path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(legacy_path)) as db:
        _ = db.execute(
            """
            CREATE TABLE agent_memory_selections (
                selection_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL
            )
            """
        )
        _ = db.execute(
            "INSERT INTO agent_memory_selections VALUES (?, ?)",
            (legacy_receipt.selection_id, legacy_receipt.model_dump_json()),
        )
        db.commit()

    # When
    migrated = SQLiteMemoryStore(legacy_path)
    actual = latest_receipt_id(migrated, actor)

    # Then
    assert actual is None
