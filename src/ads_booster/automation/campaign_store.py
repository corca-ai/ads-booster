from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override
from uuid import uuid4

from pydantic import ConfigDict, TypeAdapter, ValidationError

from ads_booster.automation.campaign_models import (
    CampaignCreate,
    CampaignId,
    CampaignRecord,
    CampaignState,
)
from ads_booster.automation.database import AutomationDatabase
from ads_booster.automation.errors import CampaignNotFoundError, CampaignRevisionError

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from ads_booster.automation.models import QueueId
    from ads_booster.workspace import WorkspaceId
    from ads_booster.workspace.database import SqliteCursor, SqliteRow

type CampaignRow = tuple[str, str, str, str, int, str | None, int, float, float]
_CAMPAIGN_ROW_ADAPTER: TypeAdapter[CampaignRow] = TypeAdapter(
    CampaignRow,
    config=ConfigDict(strict=True),
)


@dataclass(frozen=True, slots=True)
class CampaignStoreCorruptionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "generation campaign database row is corrupt"


class CampaignStore:
    _database: AutomationDatabase

    def __init__(self, home: Path) -> None:
        """Open the durable campaign store inside the configured agent home."""
        self._database = AutomationDatabase(home)

    def create(self, value: CampaignCreate) -> CampaignRecord:
        campaign_id = CampaignId(uuid4().hex)
        now = datetime.now(UTC)
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO generation_campaigns VALUES (?, ?, ?, 'active', 0, NULL, 1, ?, ?)",
                (
                    campaign_id,
                    value.workspace_id,
                    value.model_dump_json(),
                    now.timestamp(),
                    now.timestamp(),
                ),
            )
        return self.get(value.workspace_id, campaign_id)

    def get(self, workspace_id: WorkspaceId, campaign_id: CampaignId) -> CampaignRecord:
        with self._database.connect() as connection:
            row = _fetchone(
                connection,
                """
                SELECT campaign_id, workspace_id, spec_json, state, next_variation,
                    current_queue_id, revision, created_at, updated_at
                FROM generation_campaigns WHERE workspace_id = ? AND campaign_id = ?
                """,
                (workspace_id, campaign_id),
            )
        if row is None:
            raise CampaignNotFoundError(workspace_id, campaign_id)
        return _record(row)

    def list_workspace(self, workspace_id: WorkspaceId) -> tuple[CampaignRecord, ...]:
        return self._list(
            """
            SELECT campaign_id, workspace_id, spec_json, state, next_variation,
                current_queue_id, revision, created_at, updated_at
            FROM generation_campaigns WHERE workspace_id = ? ORDER BY created_at, campaign_id
            """,
            (workspace_id,),
        )

    def list_active(self) -> tuple[CampaignRecord, ...]:
        return self._list(
            """
            SELECT campaign_id, workspace_id, spec_json, state, next_variation,
                current_queue_id, revision, created_at, updated_at
            FROM generation_campaigns WHERE state = 'active' ORDER BY created_at, campaign_id
            """,
        )

    def mark_enqueued(
        self,
        campaign_id: CampaignId,
        *,
        queue_id: QueueId,
        expected_revision: int,
    ) -> CampaignRecord:
        return self._transition(
            campaign_id,
            expected_revision=expected_revision,
            query="""
                UPDATE generation_campaigns SET current_queue_id = ?,
                    next_variation = next_variation + 1, revision = revision + 1,
                    updated_at = ? WHERE campaign_id = ? AND revision = ?
            """,
            values=(queue_id,),
        )

    def complete(self, campaign_id: CampaignId, *, expected_revision: int) -> CampaignRecord:
        return self._transition(
            campaign_id,
            expected_revision=expected_revision,
            query="""
                UPDATE generation_campaigns SET state = 'completed', current_queue_id = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE campaign_id = ? AND revision = ?
            """,
        )

    def stop(
        self,
        workspace_id: WorkspaceId,
        campaign_id: CampaignId,
        *,
        expected_revision: int,
    ) -> CampaignRecord:
        record = self.get(workspace_id, campaign_id)
        return self._transition(
            record.campaign_id,
            expected_revision=expected_revision,
            query="""
                UPDATE generation_campaigns SET state = 'stopped', revision = revision + 1,
                    updated_at = ? WHERE campaign_id = ? AND revision = ?
            """,
        )

    def _transition(
        self,
        campaign_id: CampaignId,
        *,
        expected_revision: int,
        query: str,
        values: tuple[str, ...] = (),
    ) -> CampaignRecord:
        now = datetime.now(UTC)
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                query,
                (*values, now.timestamp(), campaign_id, expected_revision),
            )
            if result.rowcount != 1:
                raise CampaignRevisionError(campaign_id, expected_revision)
            row = _fetchone(
                connection,
                """
                SELECT campaign_id, workspace_id, spec_json, state, next_variation,
                    current_queue_id, revision, created_at, updated_at
                FROM generation_campaigns WHERE campaign_id = ?
                """,
                (campaign_id,),
            )
        if row is None:
            raise CampaignRevisionError(campaign_id, expected_revision)
        return _record(row)

    def _list(
        self,
        query: str,
        parameters: tuple[str, ...] = (),
    ) -> tuple[CampaignRecord, ...]:
        with self._database.connect() as connection:
            rows = _fetchall(connection, query, parameters)
        return tuple(_record(_parse_row(row)) for row in rows)


def _fetchone(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[str, ...],
) -> CampaignRow | None:
    cursor: SqliteCursor = connection.execute(query, parameters)
    row = _cursor_fetchone(cursor)
    return None if row is None else _parse_row(row)


def _fetchall(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[str, ...],
) -> list[SqliteRow]:
    cursor: SqliteCursor = connection.execute(query, parameters)
    return cursor.fetchall()


def _cursor_fetchone(cursor: SqliteCursor) -> SqliteRow | None:
    return cursor.fetchone()


def _parse_row(row: SqliteRow) -> CampaignRow:
    try:
        return _CAMPAIGN_ROW_ADAPTER.validate_python(row)
    except ValidationError as error:
        raise CampaignStoreCorruptionError from error


def _record(row: CampaignRow) -> CampaignRecord:
    spec = CampaignCreate.model_validate_json(row[2])
    return CampaignRecord(
        workspace_id=spec.workspace_id,
        name=spec.name,
        persona=spec.persona,
        promotion_material=spec.promotion_material,
        reference_images=spec.reference_images,
        reference_date=spec.reference_date,
        device=spec.device,
        variation_count=spec.variation_count,
        campaign_id=CampaignId(row[0]),
        state=CampaignState(row[3]),
        next_variation=row[4],
        current_queue_id=row[5],
        revision=row[6],
        created_at=datetime.fromtimestamp(row[7], UTC),
        updated_at=datetime.fromtimestamp(row[8], UTC),
    )
