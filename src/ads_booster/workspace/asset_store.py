from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from ads_booster.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from ads_booster.workspace.errors import (
    ScopedRecordNotFoundError,
    UnsafeAssetPathError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import AssetCreate, AssetId, AssetRecord, ContextId, WorkspaceId

if TYPE_CHECKING:
    import sqlite3

type AssetRow = tuple[str, str, str | None, str, str, str, str, int, float]

_ASSET: Final = "asset"
_CONTEXT: Final = "context"
_ASSET_DIRECTORY: Final = "assets"


class AssetStore(WorkspaceRepositoryBase):
    def create_asset(self, workspace_id: WorkspaceId, value: AssetCreate) -> AssetRecord:
        self._validate_asset_path(value.relative_path)
        asset_id = AssetId(uuid4().hex)
        now = time.time()
        with self._database.connect(write=True) as connection:
            _require_context(connection, workspace_id, value.context_id)
            _ = connection.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    asset_id,
                    value.context_id,
                    value.filename,
                    value.media_type,
                    value.relative_path,
                    value.sha256,
                    value.size_bytes,
                    now,
                ),
            )
        return AssetRecord(
            workspace_id=workspace_id,
            asset_id=asset_id,
            context_id=value.context_id,
            filename=value.filename,
            media_type=value.media_type,
            relative_path=value.relative_path,
            sha256=value.sha256,
            size_bytes=value.size_bytes,
            created_at=now,
        )

    def list_assets(self, workspace_id: WorkspaceId) -> tuple[AssetRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, asset_id, context_id, filename, media_type, relative_path,
                sha256, size_bytes, created_at
                FROM assets WHERE workspace_id = ? ORDER BY created_at, asset_id
                """,
                (workspace_id,),
            )
            rows: list[AssetRecord] = []
            while (row := _fetch_asset(cursor)) is not None:
                rows.append(_asset_from_row(row))
        return tuple(rows)

    def get_asset(self, workspace_id: WorkspaceId, asset_id: AssetId) -> AssetRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, asset_id, context_id, filename, media_type, relative_path,
                sha256, size_bytes, created_at
                FROM assets WHERE workspace_id = ? AND asset_id = ?
                """,
                (workspace_id, asset_id),
            )
            row = _fetch_asset(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_ASSET, record_id=asset_id)
        return _asset_from_row(row)

    def update_asset(
        self,
        workspace_id: WorkspaceId,
        asset_id: AssetId,
        value: AssetCreate,
    ) -> AssetRecord:
        self._validate_asset_path(value.relative_path)
        with self._database.connect(write=True) as connection:
            _require_context(connection, workspace_id, value.context_id)
            result = connection.execute(
                """
                UPDATE assets SET context_id = ?, filename = ?, media_type = ?,
                relative_path = ?, sha256 = ?, size_bytes = ?
                WHERE workspace_id = ? AND asset_id = ?
                """,
                (
                    value.context_id,
                    value.filename,
                    value.media_type,
                    value.relative_path,
                    value.sha256,
                    value.size_bytes,
                    workspace_id,
                    asset_id,
                ),
            )
            if result.rowcount != 1:
                raise ScopedRecordNotFoundError(record_type=_ASSET, record_id=asset_id)
        return self.get_asset(workspace_id, asset_id)

    def delete_asset(self, workspace_id: WorkspaceId, asset_id: AssetId) -> None:
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                "DELETE FROM assets WHERE workspace_id = ? AND asset_id = ?",
                (workspace_id, asset_id),
            )
            if result.rowcount != 1:
                raise ScopedRecordNotFoundError(record_type=_ASSET, record_id=asset_id)

    def _validate_asset_path(self, relative_path: str) -> None:
        try:
            workspace_root = self._database.path.parent.resolve()
            asset_root = workspace_root / _ASSET_DIRECTORY
            candidate = workspace_root / relative_path
            resolved = candidate.resolve(strict=False)
        except OSError as error:
            raise UnsafeAssetPathError(relative_path) from error
        if asset_root.is_symlink() or not resolved.is_relative_to(asset_root):
            raise UnsafeAssetPathError(relative_path)
        current = candidate
        while current != workspace_root:
            if current.is_symlink():
                raise UnsafeAssetPathError(relative_path)
            parent = current.parent
            if parent == current:
                raise UnsafeAssetPathError(relative_path)
            current = parent


def _require_context(
    connection: sqlite3.Connection,
    workspace_id: WorkspaceId,
    context_id: ContextId | None,
) -> None:
    if context_id is None:
        return
    cursor: SqliteCursor = connection.execute(
        "SELECT 1 FROM contexts WHERE workspace_id = ? AND context_id = ?",
        (workspace_id, context_id),
    )
    if cursor.fetchone() is None:
        raise ScopedRecordNotFoundError(record_type=_CONTEXT, record_id=context_id)


def _asset_from_row(row: AssetRow) -> AssetRecord:
    context_id = None if row[2] is None else ContextId(row[2])
    return AssetRecord(
        workspace_id=WorkspaceId(row[0]),
        asset_id=AssetId(row[1]),
        context_id=context_id,
        filename=row[3],
        media_type=row[4],
        relative_path=row[5],
        sha256=row[6],
        size_bytes=row[7],
        created_at=row[8],
    )


def _fetch_asset(cursor: SqliteCursor) -> AssetRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as asset_id,
            (str() | None) as context_id,
            str() as filename,
            str() as media_type,
            str() as relative_path,
            str() as sha256,
            int() as size_bytes,
            float() as created_at,
        ):
            return (
                workspace_id,
                asset_id,
                context_id,
                filename,
                media_type,
                relative_path,
                sha256,
                size_bytes,
                created_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_ASSET)
