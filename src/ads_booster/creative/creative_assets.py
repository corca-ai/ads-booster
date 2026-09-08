"""Scoped immutable local assets; stale lineage is a derived projection."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope

if TYPE_CHECKING:
    from collections.abc import Generator


class SqliteCreativeAssetRepository:
    def __init__(self, database: Path, artifact_root: Path) -> None:
        self.database: Path = database
        artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.artifact_root: Path = artifact_root.resolve(strict=True)
        with self._connect() as connection:
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS creative_assets (scope TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope,
                    asset_id,
                    revision))"""
            )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _scope(self, scope: CreativeScope) -> str:
        return canonical_sha256(scope.model_dump(mode="json"))

    def _verify_file(self, asset: CreativeAsset) -> None:
        relative = Path(asset.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("creative_artifact_outside_root")
        path = (self.artifact_root / relative).resolve(strict=True)
        if not path.is_relative_to(self.artifact_root) or not path.is_file():
            raise ValueError("creative_artifact_outside_root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
            raise ValueError("creative_artifact_digest_mismatch")

    def get(
        self,
        scope: CreativeScope,
        asset_id: str,
        revision: int | None = None,
        *,
        target_scope: CreativeScope | None = None,
    ) -> CreativeAsset | None:
        target = target_scope or scope
        if not scope.can_read(target):
            raise ValueError("creative_scope_denied")
        with self._connect() as connection:
            row = cast(
                "tuple[str, int] | None",
                connection.execute(
                    """SELECT payload FROM creative_assets
                    WHERE scope=?
                    AND asset_id=?
                    AND (? IS NULL OR revision=?)
                    ORDER BY revision DESC LIMIT 1""",
                    (self._scope(target), asset_id, revision, revision),
                ).fetchone(),
            )
        if row is None:
            return None
        asset = CreativeAsset.model_validate_json(row[0])
        self._verify_file(asset)
        return asset

    def describe(self, scope: CreativeScope, asset_id: str, revision: int) -> CreativeAsset | None:
        """List stored metadata only; execution and byte delivery must still use get()."""
        with self._connect() as connection:
            row = cast(
                "tuple[str] | None",
                connection.execute(
                    """SELECT payload FROM creative_assets
                    WHERE scope=? AND asset_id=? AND revision=?""",
                    (self._scope(scope), asset_id, revision),
                ).fetchone(),
            )
        return None if row is None else CreativeAsset.model_validate_json(row[0])

    def add(self, asset: CreativeAsset, *, actor_scope: CreativeScope) -> None:
        if actor_scope != asset.scope:
            raise ValueError("creative_scope_write_denied")
        self._verify_file(asset)
        key = self._scope(asset.scope)
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            latest = cast(
                "tuple[int, str] | None",
                connection.execute(
                    """SELECT revision,payload FROM creative_assets
                    WHERE scope=?
                    AND asset_id=?
                    ORDER BY revision DESC LIMIT 1""",
                    (key, asset.asset_id),
                ).fetchone(),
            )
            if latest and asset.revision == latest[0] and asset.model_dump_json() == latest[1]:
                return
            if asset.revision != (latest[0] + 1 if latest else 1):
                raise ValueError("creative_revision_conflict")
            for parent in asset.parents:
                row = cast(
                    "tuple[str, int] | None",
                    connection.execute(
                        """SELECT payload,stale FROM creative_assets
                        WHERE scope=? AND asset_id=? AND revision=?""",
                        (key, parent.asset_id, parent.revision),
                    ).fetchone(),
                )
                if row is None or row[1]:
                    raise ValueError("creative_parent_missing_or_stale")
                source = CreativeAsset.model_validate_json(row[0])
                self._verify_file(source)
                if source.sha256 != parent.sha256 or source.asset_id == asset.asset_id:
                    raise ValueError("creative_parent_binding_invalid")
                current = cast(
                    "tuple[int | None] | None",
                    connection.execute(
                        "SELECT MAX(revision) FROM creative_assets WHERE scope=? AND asset_id=?",
                        (key, parent.asset_id),
                    ).fetchone(),
                )
                if current is None or current[0] != parent.revision:
                    raise ValueError("creative_parent_not_current")
            _ = connection.execute(
                "INSERT INTO creative_assets(scope,asset_id,revision,payload) VALUES(?,?,?,?)",
                (key, asset.asset_id, asset.revision, asset.model_dump_json()),
            )
            if latest:
                self._invalidate(connection, key, asset.asset_id, latest[0])

    def _invalidate(
        self,
        connection: sqlite3.Connection,
        scope: str,
        asset_id: str,
        revision: int,
    ) -> None:
        pending = [(asset_id, revision)]
        visited: set[tuple[str, int]] = set()
        rows = cast(
            "list[tuple[str]]",
            connection.execute(
                "SELECT payload FROM creative_assets WHERE scope=?",
                (scope,),
            ).fetchall(),
        )
        assets = [CreativeAsset.model_validate_json(row[0]) for row in rows]
        while pending:
            parent = pending.pop()
            if parent in visited:
                continue
            visited.add(parent)
            for asset in assets:
                if any((p.asset_id, p.revision) == parent for p in asset.parents):
                    _ = connection.execute(
                        """UPDATE creative_assets SET stale=1
                        WHERE scope=? AND asset_id=? AND revision=?""",
                        (scope, asset.asset_id, asset.revision),
                    )
                    pending.append((asset.asset_id, asset.revision))

    def is_stale(self, scope: CreativeScope, asset_id: str, revision: int | None = None) -> bool:
        with self._connect() as connection:
            row = cast(
                "tuple[int] | None",
                connection.execute(
                    """SELECT stale FROM creative_assets
                    WHERE scope=?
                    AND asset_id=?
                    AND (? IS NULL OR revision=?)
                    ORDER BY revision DESC LIMIT 1""",
                    (self._scope(scope), asset_id, revision, revision),
                ).fetchone(),
            )
        if row is None:
            raise ValueError("creative_asset_not_found")
        return bool(row[0])
