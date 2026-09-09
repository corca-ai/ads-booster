from __future__ import annotations

# ruff: noqa: EM101
import json
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final
from urllib.parse import quote

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_actor import load_batch_actor
from ads_booster.knowledge.contract_types import MemoryKind, ScopeKind
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.file_paths import KnowledgeFileStoreError, MemoryRevisionTarget
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.scope_contracts import AccessScope, channel_member_scope

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from ads_booster.knowledge.memory_contracts import MemoryDocument
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext

_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_STATE_ROW: Final[TypeAdapter[tuple[str, str | None] | None]] = TypeAdapter(
    tuple[str, str | None] | None
)
_NEXT_ROW: Final[TypeAdapter[tuple[str, str, str, int] | None]] = TypeAdapter(
    tuple[str, str, str, int] | None
)
_TARGET_ROW: Final[TypeAdapter[tuple[str, int] | None]] = TypeAdapter(tuple[str, int] | None)
_HEAD_ROW: Final[TypeAdapter[tuple[str] | None]] = TypeAdapter(tuple[str] | None)


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
        if not self._has_current_authority():
            return False
        item = self._claim_next()
        if item is None:
            return False
        _ = self._dispatch(item)
        return True

    def dispatch_target(self, document_id: str, revision_id: str) -> MemoryViewDispatchResult:
        if not self._has_current_authority():
            return MemoryViewDispatchResult(
                processed=False,
                completed=False,
                code="memory_view_access_denied",
            )
        item = self._claim_target(document_id, revision_id)
        if item is None:
            return self._target_state(document_id, revision_id)
        return self._dispatch(item)

    def _has_current_authority(self) -> bool:
        if self.actor.conversation_scope.kind is not ScopeKind.CHANNEL:
            return True
        try:
            _ = load_batch_actor(
                self.repository,
                self.actor.conversation_scope,
                datetime.now(UTC),
                submitter=self.actor,
            )
        except KnowledgePolicyError:
            return False
        return True

    def _target_state(self, document_id: str, revision_id: str) -> MemoryViewDispatchResult:
        with self.repository.connection() as connection:
            row = _STATE_ROW.validate_python(
                connection.execute(
                    """SELECT state,error_code
                FROM memory_view_outbox
                WHERE workspace_id=?
                AND document_id=?
                AND revision_id=?
                AND EXISTS (SELECT 1
                FROM memory_documents AS document
                WHERE document.workspace_id=memory_view_outbox.workspace_id
                AND document.document_id=memory_view_outbox.document_id
                AND document.scope_key IN (SELECT value FROM json_each(?)))
                ORDER BY item_id LIMIT 1""",
                    (
                        self.actor.workspace_id,
                        document_id,
                        revision_id,
                        memory_maintenance_scope_keys(self.actor),
                    ),
                ).fetchone()
            )
        if row is None:
            return MemoryViewDispatchResult(
                processed=False, completed=False, code="memory_view_outbox_missing"
            )
        state, error = row
        if state == "completed":
            return MemoryViewDispatchResult(
                processed=False, completed=True, code="memory_view_already_current"
            )
        return MemoryViewDispatchResult(
            processed=False,
            completed=False,
            code="memory_view_running"
            if state == "running"
            else str(error or "memory_view_failed"),
        )

    def _claim_next(self) -> _ViewItem | None:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _NEXT_ROW.validate_python(
                connection.execute(
                    """SELECT item_id,document_id,revision_id,lease_generation
                FROM memory_view_outbox
                WHERE workspace_id=?
                AND state='pending'
                AND EXISTS (SELECT 1
                FROM memory_documents AS document
                WHERE document.workspace_id=memory_view_outbox.workspace_id
                AND document.document_id=memory_view_outbox.document_id
                AND document.scope_key IN (SELECT value FROM json_each(?)))
                ORDER BY rowid LIMIT 1""",
                    (self.actor.workspace_id, memory_maintenance_scope_keys(self.actor)),
                ).fetchone()
            )
            if row is None:
                return None
            return self._claim(connection, row[0], row[1], row[2], row[3])

    def _claim_target(self, document_id: str, revision_id: str) -> _ViewItem | None:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _TARGET_ROW.validate_python(
                connection.execute(
                    """SELECT item_id,lease_generation
                FROM memory_view_outbox
                WHERE workspace_id=?
                AND document_id=?
                AND revision_id=?
                AND state='pending'
                AND EXISTS (SELECT 1
                FROM memory_documents AS document
                WHERE document.workspace_id=memory_view_outbox.workspace_id
                AND document.document_id=memory_view_outbox.document_id
                AND document.scope_key IN (SELECT value FROM json_each(?)))
                ORDER BY item_id LIMIT 1""",
                    (
                        self.actor.workspace_id,
                        document_id,
                        revision_id,
                        memory_maintenance_scope_keys(self.actor),
                    ),
                ).fetchone()
            )
            if row is None:
                return None
            return self._claim(connection, row[0], document_id, revision_id, row[1])

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
            """UPDATE memory_view_outbox
                SET state='running',lease_generation=?,error_code=NULL
                WHERE item_id=?
                AND state='pending'
                AND lease_generation=?""",
            (next_generation, item_id, generation),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("memory_view_claim_conflict")
        return _ViewItem(item_id, document_id, revision_id, next_generation)

    def _dispatch(self, item: _ViewItem) -> MemoryViewDispatchResult:
        try:
            stored = self.repository.read_memory(self.actor, item.document_id, item.revision_id)
        except KnowledgePolicyError:
            stored = None
        if stored is None or stored.document.owned_scope not in memory_maintenance_scopes(
            self.actor
        ):
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
                stored.document,
                self.repository.files.root / published.relative_path,
                stored.body,
            )
        except KnowledgeFileStoreError, OSError:
            return self._failed(item, "memory_view_materialization_failed")
        if not self._is_current(item):
            return self._failed(item, "memory_view_superseded")
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE memory_view_outbox
                SET state='completed',error_code=NULL
                WHERE item_id=?
                AND state='running'
                AND lease_generation=?""",
                (item.item_id, item.generation),
            )
        if cursor.rowcount != 1:
            return MemoryViewDispatchResult(
                processed=True, completed=False, code="memory_view_completion_conflict"
            )
        return MemoryViewDispatchResult(
            processed=True, completed=True, code="memory_view_materialized"
        )

    def _is_current(self, item: _ViewItem) -> bool:
        with self.repository.connection() as connection:
            row = _HEAD_ROW.validate_python(
                connection.execute(
                    "SELECT revision_id FROM memory_heads WHERE workspace_id=? AND document_id=?",
                    (self.actor.workspace_id, item.document_id),
                ).fetchone()
            )
        return row is not None and row[0] == item.revision_id

    def _failed(self, item: _ViewItem, code: str) -> MemoryViewDispatchResult:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE memory_view_outbox
                SET state='failed',error_code=?
                WHERE item_id=?
                AND state='running'
                AND lease_generation=?""",
                (code, item.item_id, item.generation),
            )
        completed = cursor.rowcount == 1
        return MemoryViewDispatchResult(
            processed=True,
            completed=False,
            code=code if completed else "memory_view_failure_conflict",
        )


def _materialize_current_view(
    root: Path,
    document: MemoryDocument,
    source: Path,
    body: bytes,
) -> None:
    target = memory_view_path(root, document)
    _ensure_private_directory(target.parent, root)
    _require_private_file(source)
    if target.exists() and _same_bytes(target, body):
        return
    temporary = _temporary_view_path(target, body)
    try:
        os.link(source, temporary, follow_symlinks=False)
    except FileExistsError:
        if not _same_bytes(temporary, body):
            raise OSError("memory_view_temp_collision") from None
    try:
        _ = temporary.replace(target)
        target.chmod(_FILE_MODE)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def memory_view_path(
    root: Path,
    document: MemoryDocument,
) -> Path:
    team_root = root / "teams" / document.workspace_id
    owner = document.owned_scope
    if owner.kind in {ScopeKind.CHANNEL, ScopeKind.CHANNEL_MEMBER}:
        channel = AccessScope(
            kind=ScopeKind.CHANNEL,
            workspace_id=owner.workspace_id,
            channel_id=owner.channel_id,
        )
        team_root = team_root / "channels" / scope_key(channel)
    match document.kind:
        case MemoryKind.USER:
            if owner.kind is not ScopeKind.CHANNEL_MEMBER or owner.member_id is None:
                raise OSError("memory_view_user_owner_missing")
            member_path = quote(owner.member_id, safe="._-")
            return team_root / "users" / member_path / "USER.md"
        case MemoryKind.TEAM:
            return team_root / "TEAM.md"
        case MemoryKind.CORE:
            return team_root / "MEMORY.md"
        case MemoryKind.DAILY:
            if document.local_date is None:
                raise OSError("memory_view_daily_date_missing")
            return team_root / "memory" / f"{document.local_date}.md"
        case MemoryKind.SOUL:
            if document.brand_id is None:
                raise OSError("memory_view_soul_brand_missing")
            return team_root / "brands" / document.brand_id / "SOUL.md"


def remove_redacted_memory_view(
    root: Path, document: MemoryDocument, revision_digests: tuple[str, ...]
) -> None:
    """Remove only a materialized alias backed by a redacted canonical revision."""
    target = memory_view_path(root, document)
    try:
        _require_private_file(target)
    except FileNotFoundError:
        return
    if sha256(target.read_bytes()).hexdigest() not in revision_digests:
        return
    target.unlink()
    _fsync_directory(target.parent)


def _ensure_private_directory(directory: Path, root: Path) -> None:
    relative = directory.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        with suppress(FileExistsError):
            current.mkdir(mode=_DIRECTORY_MODE)
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


def memory_maintenance_scopes(actor: ActorContext) -> tuple[AccessScope, ...]:
    """Limit maintenance to the conversation and the authenticated member's own profile."""
    candidates = (actor.conversation_scope,)
    personal = channel_member_scope(actor)
    if personal is not None:
        candidates += (personal,)
    authorized: list[AccessScope] = []
    for scope in candidates:
        try:
            _ = authorize_read(actor=actor, target_scope=scope, at=datetime.now(UTC))
            _ = authorize_write(actor=actor, target_scope=scope, at=datetime.now(UTC))
        except KnowledgePolicyError:
            continue
        authorized.append(scope)
    return tuple(authorized)


def memory_maintenance_scope_keys(actor: ActorContext) -> str:
    return json.dumps([scope_key(scope) for scope in memory_maintenance_scopes(actor)])


__all__ = ["MemoryViewDispatchResult", "MemoryViewDispatcher"]
