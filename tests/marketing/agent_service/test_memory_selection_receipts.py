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
_PLAN_ROWS: TypeAdapter[list[tuple[int, int, int, str]]] = TypeAdapter(
    list[tuple[int, int, int, str]]
)
_SELECTION_ROW: TypeAdapter[tuple[str, str]] = TypeAdapter(tuple[str, str])
_SELECTION_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


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


def test_latest_selection_ignores_unrelated_invalid_receipt(tmp_path: Path) -> None:
    # Given
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    select(store, actor)
    expected = latest_receipt_id(store, actor)
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO agent_memory_selections(selection_id, data_json) VALUES (?, ?)",
            ("unrelated-invalid", "not-json"),
        )

    # When
    actual = latest_receipt_id(store, actor)

    # Then
    assert actual == expected


def test_latest_selection_isolated_to_its_exact_binding(tmp_path: Path) -> None:
    # Given
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    select(store, actor)
    expected = latest_receipt_id(store, actor)
    select(store, actor, run_id="run-2", now=NOW + timedelta(minutes=3))
    select(store, access(workspace="other"), now=NOW + timedelta(minutes=4))
    select(store, access(actor="other"), now=NOW + timedelta(minutes=5))

    # When
    actual = latest_receipt_id(store, actor)

    # Then
    assert actual == expected


def test_latest_selection_uses_utc_order_and_selection_id_tiebreaker(tmp_path: Path) -> None:
    # Given
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    earlier_local = datetime(2026, 9, 7, 0, 30, tzinfo=timezone(timedelta(hours=9)))
    later_utc = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)
    select(store, actor, now=earlier_local)
    select(store, actor, now=later_utc)
    select(store, actor, now=later_utc)
    with store.connect() as db:
        receipt_rows = _SELECTION_ROWS.validate_python(
            db.execute("SELECT selection_id, data_json FROM agent_memory_selections").fetchall()
        )

    # When
    actual = latest_receipt_id(store, actor)

    # Then
    matching = [row[0] for row in receipt_rows if '"selected_at":"2026-09-06T16:00:00Z"' in row[1]]
    assert actual == max(matching)


def test_latest_selection_query_uses_the_binding_index(tmp_path: Path) -> None:
    # Given
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    select(store, actor)

    # When
    with store.connect() as db:
        plan = _PLAN_ROWS.validate_python(
            db.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT data_json FROM agent_memory_selections
                WHERE run_id=? AND workspace_id=? AND product_id=? AND campaign_id=?
                    AND work_id=? AND member_id=? AND session_id=? AND actor_id=?
                ORDER BY selected_at DESC, selection_id DESC
                LIMIT 1
                """,
                (
                    "run-1",
                    actor.scope.workspace_id,
                    actor.scope.product_id,
                    actor.scope.campaign_id,
                    actor.scope.work_id,
                    actor.scope.member_id,
                    actor.scope.session_id,
                    actor.actor_id,
                ),
            ).fetchall()
        )

    # Then
    assert any("agent_memory_selection_latest" in row[3] for row in plan)


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
    actual = latest_receipt_id(migrated, actor)
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
    assert actual == receipt[0]
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
