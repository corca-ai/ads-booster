"""Scoped SQLite memory with fresh retrieval and durable selection receipts."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_memory import (
    MemoryAccess,
    MemoryNote,
    MemoryReference,
    MemorySelection,
    MemorySelectionReceipt,
    MemoryStage,
    require_aware,
)
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.performance_observation_validity import (
    learning_source_is_current as performance_learning_source_is_current,
)
from ads_booster.learning.work_observation_validity import (
    learning_source_is_current as work_learning_source_is_current,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path

_MAX_NOTES = 24
_MAX_CHARS = 24000
_MAX_QUERY = 8000
_ROWS = TypeAdapter(list[tuple[str]])
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_OPTIONAL_INT_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_SELECTION_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])
_TABLE_INFO_ROWS: TypeAdapter[list[tuple[int, str, str, int, str | None, int]]] = TypeAdapter(
    list[tuple[int, str, str, int, str | None, int]]
)
_SELECTION_BINDING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "TEXT"),
    ("workspace_id", "TEXT"),
    ("product_id", "TEXT"),
    ("campaign_id", "TEXT"),
    ("work_id", "TEXT"),
    ("member_id", "TEXT"),
    ("session_id", "TEXT"),
    ("actor_id", "TEXT"),
    ("selected_at", "TEXT"),
)


@dataclass(frozen=True, slots=True)
class SQLiteMemoryStore:
    database_path: Path

    def __post_init__(self) -> None:
        """Add isolated memory tables without altering the canonical Run ledger."""
        with self.connect() as db:
            _ = db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_memory_notes (
                    note_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, product TEXT NOT NULL,
                    campaign TEXT NOT NULL, member TEXT NOT NULL, session TEXT NOT NULL,
                    data_json TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS agent_memory_scope ON
                    agent_memory_notes(workspace,product,campaign,member,session);
                CREATE TABLE IF NOT EXISTS agent_memory_history (
                    sequence INTEGER PRIMARY KEY, note_id TEXT NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
                    data_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_memory_selections (
                    selection_id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_memory_selection_schema (
                    schema_version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_memory_retirements (
                    note_id TEXT PRIMARY KEY, successor TEXT NOT NULL);
            """)
            self._migrate_selection_bindings(db)

    @staticmethod
    def _selection_binding_values(receipt: MemorySelectionReceipt) -> tuple[str, ...]:
        """Project a receipt's durable identity into indexed SQLite columns."""
        require_aware(receipt.selected_at)
        scope = receipt.scope
        return (
            receipt.run_id,
            scope.workspace_id,
            scope.product_id,
            scope.campaign_id,
            scope.work_id,
            scope.member_id,
            scope.session_id,
            "" if receipt.actor_id is None else receipt.actor_id,
            receipt.selected_at.astimezone(UTC).isoformat(),
        )

    def _migrate_selection_bindings(self, db: sqlite3.Connection) -> None:
        """Add indexed receipt bindings once while retaining legacy payloads verbatim."""
        _ = db.execute("BEGIN IMMEDIATE")
        schema = _OPTIONAL_INT_ROW.validate_python(
            db.execute(
                "SELECT schema_version FROM agent_memory_selection_schema LIMIT 1"
            ).fetchone()
        )
        if schema is not None:
            return
        existing_columns = {
            column[1]
            for column in _TABLE_INFO_ROWS.validate_python(
                db.execute("PRAGMA table_info(agent_memory_selections)").fetchall()
            )
        }
        for column, type_name in _SELECTION_BINDING_COLUMNS:
            if column not in existing_columns:
                _ = db.execute(
                    f"ALTER TABLE agent_memory_selections ADD COLUMN {column} {type_name}"
                )
        rows = _SELECTION_ROWS.validate_python(
            db.execute("SELECT selection_id, data_json FROM agent_memory_selections").fetchall()
        )
        for selection_id, data_json in rows:
            try:
                receipt = MemorySelectionReceipt.model_validate_json(data_json)
            except ValidationError:
                continue
            _ = db.execute(
                """
                UPDATE agent_memory_selections
                SET run_id=?, workspace_id=?, product_id=?, campaign_id=?, work_id=?, member_id=?,
                    session_id=?, actor_id=?, selected_at=?
                WHERE selection_id=?
                """,
                (*self._selection_binding_values(receipt), selection_id),
            )
        _ = db.execute(
            """
            CREATE INDEX IF NOT EXISTS agent_memory_selection_latest ON agent_memory_selections(
                run_id, workspace_id, product_id, campaign_id, work_id, member_id, session_id,
                actor_id, selected_at DESC, selection_id DESC
            )
            """
        )
        _ = db.execute("INSERT INTO agent_memory_selection_schema VALUES (1)")

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=1)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, note: MemoryNote, access: MemoryAccess, *, now: datetime) -> None:
        require_aware(now)
        self._write_scope(note, access)
        if note.author_id != access.actor_id or note.stage != "candidate":
            raise ValueError("memory_new_note_requires_author_and_candidate")
        if not note.created_at <= now < note.expires_at:
            raise ValueError("memory_note_not_current")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = self._get(db, note.note_id, access)
            if existing is not None:
                if existing == note:
                    return
                raise ValueError("memory_idempotency_conflict")
            for reference in (*note.conflicts, *((note.supersedes,) if note.supersedes else ())):
                previous = self._get(db, reference, access)
                if previous is None or previous.scope != note.scope:
                    raise ValueError("memory_reference_scope_denied")
                if reference == note.supersedes and note.version != previous.version + 1:
                    raise ValueError("memory_correction_version_invalid")
            scope = note.scope
            _ = db.execute(
                "INSERT INTO agent_memory_notes VALUES (?,?,?,?,?,?,?,0)",
                (
                    note.note_id,
                    scope.workspace_id,
                    scope.product_id,
                    scope.campaign_id,
                    scope.member_id,
                    scope.session_id,
                    note.model_dump_json(),
                ),
            )
            self._history(db, note, access, "created", now)

    def get(self, note_id: str, access: MemoryAccess) -> MemoryNote | None:
        """Return a scoped note, including an unreviewed candidate for human review."""
        with self.connect() as db:
            return self._get(db, note_id, access)

    def list_notes(
        self, access: MemoryAccess, *, stage: MemoryStage | None = None, limit: int = 20
    ) -> tuple[MemoryNote, ...]:
        """Expose a bounded scoped review queue, including expired notes for correction."""
        if not 1 <= limit <= _MAX_NOTES:
            raise ValueError("memory_selection_budget_invalid")
        with self.connect() as db:
            notes = self._visible(db, access)
            notes.sort(key=lambda note: (note.created_at, note.note_id), reverse=True)
            return tuple(note for note in notes if stage is None or note.stage == stage)[:limit]

    def review(
        self,
        note_id: str,
        access: MemoryAccess,
        *,
        expected_sha256: str,
        stage: MemoryStage,
        now: datetime,
    ) -> MemoryNote:
        require_aware(now)
        if not access.can_review:
            raise ValueError("memory_review_not_allowed")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            note = self._require(db, note_id, access, expected_sha256)
            if not note.created_at <= now < note.expires_at:
                raise ValueError("memory_note_not_current")
            if stage in {"review", "approved"} and not (
                work_learning_source_is_current(db, note, access)
                and performance_learning_source_is_current(db, note, access)
            ):
                raise ValueError("memory_learning_source_not_current")
            transitions: dict[str, set[str]] = {
                "candidate": {"review", "rejected"},
                "review": {"approved", "rejected"},
                "approved": {"rejected"},
                "rejected": set(),
            }
            if stage not in transitions[note.stage]:
                raise ValueError("memory_review_transition_invalid")
            if (
                stage == "approved"
                and note.supersedes
                and (
                    db.execute(
                        "SELECT successor FROM agent_memory_retirements WHERE note_id=?",
                        (note.supersedes,),
                    ).fetchone()
                    is not None
                )
            ):
                raise ValueError("memory_correction_head_changed")
            updated = note.model_copy(update={"stage": stage})
            _ = db.execute(
                "UPDATE agent_memory_notes SET data_json=? WHERE note_id=?",
                (updated.model_dump_json(), note_id),
            )
            if stage == "approved" and updated.supersedes:
                _ = db.execute(
                    "INSERT INTO agent_memory_retirements VALUES (?,?)",
                    (updated.supersedes, updated.note_id),
                )
            self._history(db, updated, access, stage, now)
            return updated

    def delete(
        self, note_id: str, access: MemoryAccess, *, expected_sha256: str, now: datetime
    ) -> None:
        """Tombstone retrieval; retain source history, never claim physical erasure."""
        require_aware(now)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            note = self._require(db, note_id, access, expected_sha256)
            if note.author_id != access.actor_id and not access.can_review:
                raise ValueError("memory_delete_not_allowed")
            _ = db.execute("UPDATE agent_memory_notes SET deleted=1 WHERE note_id=?", (note_id,))
            self._history(db, note, access, "deleted", now)

    def select(  # noqa: PLR0913 - explicit scope, lineage and retrieval budgets.
        self,
        access: MemoryAccess,
        *,
        query: str,
        run_id: str,
        now: datetime,
        limit: int = 6,
        max_chars: int = 6000,
    ) -> MemorySelection:
        require_aware(now)
        if (
            not 1 <= limit <= _MAX_NOTES
            or not 1 <= max_chars <= _MAX_CHARS
            or len(query) > _MAX_QUERY
        ):
            raise ValueError("memory_selection_budget_invalid")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            # Filter authorization in SQL before text is loaded or ranked.
            notes = self._visible(db, access)
            superseded = {
                note.note_id
                for note in notes
                if db.execute(
                    "SELECT successor FROM agent_memory_retirements WHERE note_id=?",
                    (note.note_id,),
                ).fetchone()
                is not None
            }
            eligible = [
                n
                for n in notes
                if n.stage == "approved"
                and n.note_id not in superseded
                and n.created_at <= now < n.expires_at
                and work_learning_source_is_current(db, n, access)
                and performance_learning_source_is_current(db, n, access)
            ]
            terms = set(
                TypeAdapter(list[str]).validate_python(re.findall(r"\w+", query.casefold()))
            )
            scored = [(sum(term in n.text.casefold() for term in terms), n) for n in eligible]
            scored.sort(
                key=lambda pair: (pair[0], pair[1].created_at, pair[1].note_id), reverse=True
            )
            selected: list[MemoryNote] = []
            used = 0
            for score, note in scored:
                size = len(note.model_dump_json())
                if (terms and not score) or used + size > max_chars:
                    continue
                selected.append(note)
                used += size
                if len(selected) == limit:
                    break
            receipt = MemorySelectionReceipt(
                selection_id=f"memory-selection-{uuid4().hex}",
                run_id=run_id,
                scope=access.scope,
                selected=tuple(
                    MemoryReference(note_id=n.note_id, sha256=contract_sha256(n)) for n in selected
                ),
                query_sha256=contract_sha256({"query": query}),
                selected_at=now,
                actor_id=access.actor_id,
            )
            receipt = receipt.model_copy(update={"selection_sha256": receipt.canonical_sha256()})
            _ = db.execute(
                """
                INSERT INTO agent_memory_selections(
                    selection_id, data_json, run_id, workspace_id, product_id, campaign_id, work_id,
                    member_id, session_id, actor_id, selected_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt.selection_id,
                    receipt.model_dump_json(),
                    *self._selection_binding_values(receipt),
                ),
            )
            return MemorySelection(notes=tuple(selected), receipt=receipt)

    def latest_selection(
        self,
        access: MemoryAccess,
        *,
        run_id: str,
        now: datetime,
    ) -> MemorySelection | None:
        """Return the newest server selection for this exact run, scope, and actor."""
        require_aware(now)
        with self.connect() as db:
            row = _OPTIONAL_STRING_ROW.validate_python(
                db.execute(
                    """
                    SELECT data_json FROM agent_memory_selections
                    WHERE run_id=? AND workspace_id=? AND product_id=? AND campaign_id=?
                        AND work_id=? AND member_id=? AND session_id=? AND actor_id=?
                    ORDER BY selected_at DESC, selection_id DESC
                    LIMIT 1
                    """,
                    (
                        run_id,
                        access.scope.workspace_id,
                        access.scope.product_id,
                        access.scope.campaign_id,
                        access.scope.work_id,
                        access.scope.member_id,
                        access.scope.session_id,
                        access.actor_id,
                    ),
                ).fetchone()
            )
        if row is None:
            return None
        receipt = MemorySelectionReceipt.model_validate_json(row[0])
        return self.current_selection(access, receipt, now=now)

    def current_selection(
        self,
        access: MemoryAccess,
        receipt: MemorySelectionReceipt,
        *,
        now: datetime,
    ) -> MemorySelection:
        """Rebuild a persisted selection only while every approved reference stays current."""
        require_aware(now)
        if (
            receipt.scope != access.scope
            or receipt.actor_id != access.actor_id
            or receipt.selection_sha256 is None
            or receipt.selection_sha256 != receipt.canonical_sha256()
        ):
            message = "memory_selection_binding_changed"
            raise ValueError(message)
        with self.connect() as db:
            stored_row = _ROWS.validate_python(
                db.execute(
                    "SELECT data_json FROM agent_memory_selections WHERE selection_id=?",
                    (receipt.selection_id,),
                ).fetchall()
            )
            if len(stored_row) != 1:
                message = "memory_selection_not_found"
                raise ValueError(message)
            stored = MemorySelectionReceipt.model_validate_json(stored_row[0][0])
            if stored != receipt:
                message = "memory_selection_binding_changed"
                raise ValueError(message)
            notes: list[MemoryNote] = []
            for reference in receipt.selected:
                note = self._get(db, reference.note_id, access)
                retired = _OPTIONAL_STRING_ROW.validate_python(
                    db.execute(
                        "SELECT successor FROM agent_memory_retirements WHERE note_id=?",
                        (reference.note_id,),
                    ).fetchone()
                )
                if (
                    note is None
                    or note.stage != "approved"
                    or retired is not None
                    or not note.created_at <= now < note.expires_at
                    or not work_learning_source_is_current(db, note, access)
                    or not performance_learning_source_is_current(db, note, access)
                    or contract_sha256(note) != reference.sha256
                ):
                    message = "memory_selection_not_current"
                    raise ValueError(message)
                notes.append(note)
        return MemorySelection(notes=tuple(notes), receipt=receipt)

    def references_are_current(
        self,
        access: MemoryAccess,
        receipt: MemorySelectionReceipt,
        references: tuple[MemoryReference, ...],
        *,
        now: datetime,
    ) -> bool:
        """Recheck conflict-question references for a current actor in the same scope."""
        if (
            receipt.scope != access.scope
            or receipt.actor_id is None
            or receipt.selection_sha256 != receipt.canonical_sha256()
        ):
            return False
        if access.private and receipt.actor_id != access.actor_id:
            return False
        try:
            original_access = access.model_copy(update={"actor_id": receipt.actor_id})
            current = self.current_selection(original_access, receipt, now=now)
        except ValueError:
            return False
        selected = {item.note_id: item for item in current.receipt.selected}
        return all(selected.get(item.note_id) == item for item in references)

    @staticmethod
    def _visible(db: sqlite3.Connection, access: MemoryAccess) -> list[MemoryNote]:
        scope = access.scope
        rows = _ROWS.validate_python(
            db.execute(
                """
            SELECT data_json FROM agent_memory_notes
            WHERE workspace=? AND product IN ('',?) AND campaign IN ('',?) AND deleted=0
            AND json_extract(data_json,'$.scope.channel_id') IS ?
            AND COALESCE(json_extract(data_json,'$.scope.work_id'),'') IN ('',?)
            AND ((member='' AND session='') OR (?=1 AND member=? AND session=?))
        """,
                (
                    scope.workspace_id,
                    scope.product_id,
                    scope.campaign_id,
                    scope.channel_id,
                    scope.work_id,
                    int(access.private),
                    scope.member_id,
                    scope.session_id,
                ),
            ).fetchall()
        )
        return [MemoryNote.model_validate_json(row[0]) for row in rows]

    @classmethod
    def _get(cls, db: sqlite3.Connection, note_id: str, access: MemoryAccess) -> MemoryNote | None:
        return next((n for n in cls._visible(db, access) if n.note_id == note_id), None)

    @staticmethod
    def _write_scope(note: MemoryNote, access: MemoryAccess) -> None:
        scope = note.scope
        current = access.scope
        if scope.workspace_id != current.workspace_id:
            raise ValueError("memory_write_scope_denied")
        if (
            scope.channel_id != current.channel_id
            or scope.product_id != current.product_id
            or scope.campaign_id != current.campaign_id
            or scope.work_id != current.work_id
        ):
            raise ValueError("memory_write_scope_denied")
        if access.private:
            if scope.member_id != current.member_id or scope.session_id != current.session_id:
                raise ValueError("memory_private_shared_read_only")
        elif scope.member_id or scope.session_id:
            raise ValueError("memory_write_scope_denied")

    @classmethod
    def _require(
        cls, db: sqlite3.Connection, note_id: str, access: MemoryAccess, expected: str
    ) -> MemoryNote:
        note = cls._get(db, note_id, access)
        if note is None:
            raise ValueError("memory_not_found")
        cls._write_scope(note, access)
        if contract_sha256(note) != expected:
            raise ValueError("memory_revision_changed")
        return note

    @staticmethod
    def _history(
        db: sqlite3.Connection, note: MemoryNote, access: MemoryAccess, action: str, now: datetime
    ) -> None:
        _ = db.execute(
            """INSERT INTO agent_memory_history(note_id,action,actor,at,data_json)
            VALUES (?,?,?,?,?)""",
            (note.note_id, action, access.actor_id, now.isoformat(), note.model_dump_json()),
        )
