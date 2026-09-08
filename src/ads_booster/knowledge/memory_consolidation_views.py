from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from ads_booster.knowledge.contract_types import MemoryKind
from ads_booster.knowledge.file_paths import KnowledgeFileStoreError, MemoryRevisionTarget
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.scope_contracts import ActorContext

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


@dataclass(frozen=True, slots=True)
class MemoryViewDispatchResult:
    processed: bool
    completed: bool
    code: str


@dataclass(frozen=True, slots=True)
class _ViewItem:
    item_id: str
    document_id: str
    revision_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class MemoryViewDispatcher:
    repository: SqliteKnowledgeRepository
    actor: ActorContext

    def dispatch_once(self) -> bool:
        item = self._claim_next()
        if item is None:
            return False
        _ = self._dispatch(item)
        return True

    def dispatch_target(self, document_id: str, revision_id: str) -> MemoryViewDispatchResult:
        item = self._claim_target(document_id, revision_id)
        if item is None:
            return self._target_state(document_id, revision_id)
        return self._dispatch(item)

    def _target_state(self, document_id: str, revision_id: str) -> MemoryViewDispatchResult:
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT state,error_code FROM memory_view_outbox "
                + "WHERE workspace_id=? AND document_id=? AND revision_id=? "
                + "ORDER BY item_id LIMIT 1",
                (self.actor.workspace_id, document_id, revision_id),
            ).fetchone()
        if row is None:
            return MemoryViewDispatchResult(False, False, "memory_view_outbox_missing")
        state, error = str(row[0]), row[1]
        if state == "completed":
            return MemoryViewDispatchResult(False, True, "memory_view_already_current")
        return MemoryViewDispatchResult(
            False,
            False,
            "memory_view_running" if state == "running" else str(error or "memory_view_failed"),
        )

    def _claim_next(self) -> _ViewItem | None:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT item_id,document_id,revision_id,lease_generation "
                + "FROM memory_view_outbox "
                + "WHERE workspace_id=? AND state='pending' ORDER BY rowid LIMIT 1",
                (self.actor.workspace_id,),
            ).fetchone()
            if row is None:
                return None
            return self._claim(connection, str(row[0]), str(row[1]), str(row[2]), int(row[3]))

    def _claim_target(self, document_id: str, revision_id: str) -> _ViewItem | None:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT item_id,lease_generation FROM memory_view_outbox "
                + "WHERE workspace_id=? AND document_id=? AND revision_id=? AND state='pending' "
                + "ORDER BY item_id LIMIT 1",
                (self.actor.workspace_id, document_id, revision_id),
            ).fetchone()
            if row is None:
                return None
            return self._claim(connection, str(row[0]), document_id, revision_id, int(row[1]))

    def _claim(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        document_id: str,
        revision_id: str,
        generation: int,
    ) -> _ViewItem:
        next_generation = generation + 1
        cursor = connection.execute(
            "UPDATE memory_view_outbox "
            + "SET state='running',lease_generation=?,error_code=NULL "
            + "WHERE item_id=? AND state='pending' AND lease_generation=?",
            (next_generation, item_id, generation),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("memory_view_claim_conflict")
        return _ViewItem(item_id, document_id, revision_id, next_generation)

    def _dispatch(self, item: _ViewItem) -> MemoryViewDispatchResult:
        stored = self.repository.read_memory(self.actor, item.document_id, item.revision_id)
        if stored is None:
            return self._failed(item, "memory_view_missing")
        if not self._is_current(item):
            return self._failed(item, "memory_view_superseded")
        try:
            published = self.repository.files.published(
                MemoryRevisionTarget(
                    workspace_id=stored.document.workspace_id,
                    document_id=stored.document.document_id,
                    revision_id=stored.revision.revision_id,
                ),
                stored.revision.body_sha256,
            )
            _materialize_current_view(
                self.repository.files.root,
                stored.document.kind,
                stored.document.workspace_id,
                stored.document.brand_id,
                stored.document.local_date,
                self.repository.files.root / published.relative_path,
                stored.body,
            )
        except (KnowledgeFileStoreError, OSError):
            return self._failed(item, "memory_view_materialization_failed")
        if not self._is_current(item):
            return self._failed(item, "memory_view_superseded")
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE memory_view_outbox SET state='completed',error_code=NULL "
                + "WHERE item_id=? AND state='running' AND lease_generation=?",
                (item.item_id, item.generation),
            )
        if cursor.rowcount != 1:
            return MemoryViewDispatchResult(True, False, "memory_view_completion_conflict")
        return MemoryViewDispatchResult(True, True, "memory_view_materialized")

    def _is_current(self, item: _ViewItem) -> bool:
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT revision_id FROM memory_heads "
                + "WHERE workspace_id=? AND document_id=?",
                (self.actor.workspace_id, item.document_id),
            ).fetchone()
        return row is not None and str(row[0]) == item.revision_id

    def _failed(self, item: _ViewItem, code: str) -> MemoryViewDispatchResult:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE memory_view_outbox SET state='failed',error_code=? "
                + "WHERE item_id=? AND state='running' AND lease_generation=?",
                (code, item.item_id, item.generation),
            )
        completed = cursor.rowcount == 1
        return MemoryViewDispatchResult(True, False, code if completed else "memory_view_failure_conflict")


def _materialize_current_view(
    root: Path,
    kind: MemoryKind,
    workspace_id: str,
    brand_id: str | None,
    local_date: date | None,
    source: Path,
    body: bytes,
) -> None:
    target = _view_path(root, kind, workspace_id, brand_id, local_date)
    _ensure_private_directory(target.parent, root)
    _require_private_file(source)
    if target.exists() and _same_bytes(target, body):
        return
    temporary = _temporary_view_path(target, body)
    try:
        os.link(source, temporary, follow_symlinks=False)
    except FileExistsError:
        if not _same_bytes(temporary, body):
            raise OSError("memory_view_temp_collision")
    try:
        os.replace(temporary, target)
        os.chmod(target, _FILE_MODE)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _view_path(
    root: Path,
    kind: MemoryKind,
    workspace_id: str,
    brand_id: str | None,
    local_date: date | None,
) -> Path:
    team_root = root / "teams" / workspace_id
    match kind:
        case MemoryKind.TEAM:
            return team_root / "TEAM.md"
        case MemoryKind.CORE:
            return team_root / "MEMORY.md"
        case MemoryKind.DAILY:
            if local_date is None:
                raise OSError("memory_view_daily_date_missing")
            return team_root / "memory" / f"{local_date}.md"
        case MemoryKind.SOUL:
            if brand_id is None:
                raise OSError("memory_view_soul_brand_missing")
            return team_root / "brands" / brand_id / "SOUL.md"


def _ensure_private_directory(directory: Path, root: Path) -> None:
    relative = directory.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        try:
            current.mkdir(mode=_DIRECTORY_MODE)
        except FileExistsError:
            pass
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError("memory_view_directory_unsafe")
        if stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE:
            raise OSError("memory_view_directory_permissions_unsafe")


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OSError("memory_view_source_unsafe")
    if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
        raise OSError("memory_view_source_permissions_unsafe")


def _same_bytes(path: Path, expected: bytes) -> bool:
    try:
        _require_private_file(path)
        return path.read_bytes() == expected
    except FileNotFoundError:
        return False


def _temporary_view_path(target: Path, body: bytes) -> Path:
    return target.parent / f".{target.name}.{sha256(body).hexdigest()}.view"


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["MemoryViewDispatcher", "MemoryViewDispatchResult"]
