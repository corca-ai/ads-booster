"""Shared Run-to-asset projection for authenticated upload and approved tool intake."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@contextmanager
def asset_links(database: Path) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(database)
    try:
        with connection:
            _ = connection.execute("""CREATE TABLE IF NOT EXISTS creative_run_assets (
                tenant_id TEXT NOT NULL,run_id TEXT NOT NULL,asset_id TEXT NOT NULL,
                revision INTEGER NOT NULL,request_sha256 TEXT NOT NULL,actor_id TEXT NOT NULL,
                PRIMARY KEY(tenant_id,run_id,asset_id,revision))""")
            yield connection
    finally:
        connection.close()


def link_asset(  # noqa: PLR0913 - exact Run, asset revision, request and actor binding.
    database: Path,
    *,
    tenant_id: str,
    run_id: str,
    asset_id: str,
    revision: int,
    request_sha256: str,
    actor_id: str,
) -> None:
    with asset_links(database) as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        prior = cast(
            "tuple[str, str] | None",
            connection.execute(
                """SELECT request_sha256,actor_id FROM creative_run_assets
                WHERE tenant_id=? AND run_id=? AND asset_id=? AND revision=?""",
                (tenant_id, run_id, asset_id, revision),
            ).fetchone(),
        )
        if prior is not None and prior != (request_sha256, actor_id):
            raise ValueError("creative_upload_idempotency_conflict")
        _ = connection.execute(
            "INSERT OR IGNORE INTO creative_run_assets VALUES(?,?,?,?,?,?)",
            (tenant_id, run_id, asset_id, revision, request_sha256, actor_id),
        )
