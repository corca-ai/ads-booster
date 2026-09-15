from __future__ import annotations

import hashlib
import hmac
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe

from pydantic import TypeAdapter

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.threads.drafts import (
    ThreadsAssetReference,
    ThreadsDraftBatch,
    ThreadsDraftRepository,
    ThreadsDraftState,
)


class ThreadsMediaError(ValueError):
    pass


_ROW: TypeAdapter[tuple[str, str, int, str, str, int, str, str] | None] = TypeAdapter(
    tuple[str, str, int, str, str, int, str, str] | None
)
_TABLE_INFO: TypeAdapter[list[tuple[int, str, str, int, str | None, int]]] = TypeAdapter(
    list[tuple[int, str, str, int, str | None, int]]
)


@dataclass(frozen=True, slots=True)
class ThreadsMediaGrant:
    url: str
    asset: ThreadsAssetReference
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ThreadsMediaDelivery:
    database_path: Path
    artifact_root: Path
    public_origin: str
    signing_secret: bytes

    def __post_init__(self) -> None:
        if not self.public_origin.startswith("https://") or len(self.signing_secret) < 32:
            raise ThreadsMediaError("threads_media_configuration_invalid")
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute(
                """CREATE TABLE IF NOT EXISTS threads_media_grants (
                token_sha256 TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                batch_revision INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_revision INTEGER NOT NULL,
                asset_sha256 TEXT NOT NULL,
                expires_at TEXT NOT NULL
                )"""
            )
            columns = {
                row[1]
                for row in _TABLE_INFO.validate_python(
                    database.execute("PRAGMA table_info(threads_media_grants)").fetchall()
                )
            }
            if "item_id" not in columns:
                _ = database.execute(
                    "ALTER TABLE threads_media_grants ADD COLUMN item_id TEXT NOT NULL DEFAULT ''"
                )

    def issue(
        self,
        batch: ThreadsDraftBatch,
        *,
        item_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> tuple[ThreadsMediaGrant, ...]:
        if (
            batch.state not in {ThreadsDraftState.DRAFT, ThreadsDraftState.APPROVED}
            or expires_at <= now
        ):
            raise ThreadsMediaError("threads_media_current_draft_required")
        item = next((candidate for candidate in batch.items if candidate.item_id == item_id), None)
        if item is None or item.excluded or not item.assets:
            raise ThreadsMediaError("threads_media_draft_item_invalid")
        grants: list[ThreadsMediaGrant] = []
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute("BEGIN IMMEDIATE")
            for asset in item.assets:
                token = token_urlsafe(32)
                signature = hmac.new(
                    self.signing_secret, token.encode(), hashlib.sha256
                ).hexdigest()
                credential = f"{token}.{signature}"
                _ = database.execute(
                    """INSERT INTO threads_media_grants(
                    token_sha256,workspace_id,batch_id,batch_revision,item_id,asset_id,
                    asset_revision,asset_sha256,expires_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        hashlib.sha256(credential.encode()).hexdigest(),
                        batch.workspace_id,
                        batch.batch_id,
                        batch.revision,
                        item.item_id,
                        asset.asset_id,
                        asset.revision,
                        asset.sha256,
                        expires_at.isoformat(),
                    ),
                )
                grants.append(
                    ThreadsMediaGrant(
                        f"{self.public_origin.rstrip('/')}/integrations/threads/media/{credential}",
                        asset,
                        expires_at,
                    )
                )
        return tuple(grants)

    def read(self, credential: str, *, now: datetime) -> bytes:
        token, separator, signature = credential.partition(".")
        expected = hmac.new(self.signing_secret, token.encode(), hashlib.sha256).hexdigest()
        if not separator or not hmac.compare_digest(signature, expected):
            raise ThreadsMediaError("threads_media_grant_invalid")
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _ROW.validate_python(
                database.execute(
                    """SELECT workspace_id,batch_id,batch_revision,item_id,asset_id,asset_revision,
                    asset_sha256,expires_at
                    FROM threads_media_grants WHERE token_sha256=?""",
                    (hashlib.sha256(credential.encode()).hexdigest(),),
                ).fetchone()
            )
        if row is None or datetime.fromisoformat(row[7]) <= now:
            raise ThreadsMediaError("threads_media_grant_expired")
        (
            workspace_id,
            batch_id,
            batch_revision,
            item_id,
            asset_id,
            asset_revision,
            asset_sha256,
            _,
        ) = row
        batch = ThreadsDraftRepository(self.database_path).get(workspace_id, batch_id)
        if (
            batch is None
            or batch.revision != batch_revision
            or batch.state not in {ThreadsDraftState.DRAFT, ThreadsDraftState.APPROVED}
        ):
            raise ThreadsMediaError("threads_media_draft_changed")
        reference = next(
            (
                asset
                for item in batch.items
                if item.item_id == item_id and not item.excluded
                for asset in item.assets
                if asset.asset_id == asset_id
                and asset.revision == asset_revision
                and asset.sha256 == asset_sha256
            ),
            None,
        )
        if reference is None:
            raise ThreadsMediaError("threads_media_asset_changed")
        scope = CreativeScope(workspace_id=workspace_id, product_id="trace")
        repository = SqliteCreativeAssetRepository(self.database_path, self.artifact_root)
        asset = repository.get(scope, reference.asset_id, reference.revision)
        if asset is None or asset.sha256 != reference.sha256:
            raise ThreadsMediaError("threads_media_asset_unavailable")
        return (repository.artifact_root / asset.relative_path).read_bytes()


__all__ = ["ThreadsMediaDelivery", "ThreadsMediaError", "ThreadsMediaGrant"]
