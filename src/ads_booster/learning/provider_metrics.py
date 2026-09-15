from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from ads_booster.contracts.provider_metrics import (
    ProviderMetricAvailability,
    ProviderMetricChange,
    ProviderMetricSnapshot,
)

_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_OPTIONAL_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


class ProviderMetricConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderMetricRepository:
    database_path: Path

    def __post_init__(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_metric_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    period TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_metric_series
                    ON provider_metric_snapshots(
                        workspace_id,connection_id,subject_kind,subject_id,metric,period,observed_at
                    );
                CREATE TRIGGER IF NOT EXISTS provider_metric_snapshots_immutable
                BEFORE UPDATE ON provider_metric_snapshots BEGIN
                    SELECT RAISE(ABORT, 'provider metric snapshots are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS provider_metric_snapshots_append_only
                BEFORE DELETE ON provider_metric_snapshots BEGIN
                    SELECT RAISE(ABORT, 'provider metric snapshots are append-only');
                END;
                """
            )

    def append(self, snapshot: ProviderMetricSnapshot) -> ProviderMetricSnapshot:
        try:
            with closing(sqlite3.connect(self.database_path)) as database, database:
                _ = database.execute(
                    """INSERT INTO provider_metric_snapshots(
                    snapshot_id,provider,workspace_id,connection_id,subject_kind,subject_id,
                    metric,period,observed_at,snapshot_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot.snapshot_id,
                        snapshot.provider,
                        snapshot.workspace_id,
                        snapshot.connection_id,
                        snapshot.subject_kind,
                        snapshot.subject_id,
                        snapshot.metric,
                        snapshot.period,
                        snapshot.observed_at.isoformat(),
                        snapshot.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            current = self.get(snapshot.snapshot_id)
            if current == snapshot:
                return snapshot
            raise ProviderMetricConflictError("provider_metric_snapshot_conflict") from error
        return snapshot

    def get(self, snapshot_id: str) -> ProviderMetricSnapshot | None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    "SELECT snapshot_json FROM provider_metric_snapshots WHERE snapshot_id=?",
                    (snapshot_id,),
                ).fetchone()
            )
        return None if row is None else ProviderMetricSnapshot.model_validate_json(row[0])

    def series(
        self,
        *,
        workspace_id: str,
        connection_id: str,
        subject_kind: str,
        subject_id: str,
        metric: str,
        limit: int = 100,
    ) -> tuple[ProviderMetricSnapshot, ...]:
        if limit < 1 or limit > 1000:
            raise ProviderMetricConflictError("provider_metric_limit_invalid")
        with closing(sqlite3.connect(self.database_path)) as database, database:
            rows = _ROWS.validate_python(
                database.execute(
                    """SELECT snapshot_json FROM provider_metric_snapshots
                    WHERE workspace_id=? AND connection_id=? AND subject_kind=?
                    AND subject_id=? AND metric=? ORDER BY observed_at DESC LIMIT ?""",
                    (
                        workspace_id,
                        connection_id,
                        subject_kind,
                        subject_id,
                        metric,
                        limit,
                    ),
                ).fetchall()
            )
        return tuple(ProviderMetricSnapshot.model_validate_json(row[0]) for row in rows)


def compare_provider_metrics(
    previous: ProviderMetricSnapshot, current: ProviderMetricSnapshot
) -> ProviderMetricChange:
    identity = (
        previous.provider,
        previous.workspace_id,
        previous.connection_id,
        previous.subject_kind,
        previous.subject_id,
        previous.metric,
    )
    current_identity = (
        current.provider,
        current.workspace_id,
        current.connection_id,
        current.subject_kind,
        current.subject_id,
        current.metric,
    )
    if identity != current_identity:
        return _change(previous, current, False, None, "identity_mismatch")
    if current.observed_at <= previous.observed_at:
        return _change(previous, current, False, None, "observation_order_invalid")
    if (
        previous.period != current.period
        or previous.provider_api_version != current.provider_api_version
    ):
        return _change(previous, current, False, None, "definition_mismatch")
    if (
        previous.availability is not ProviderMetricAvailability.AVAILABLE
        or current.availability is not ProviderMetricAvailability.AVAILABLE
        or previous.value is None
        or current.value is None
    ):
        return _change(previous, current, False, None, "unavailable")
    if current.value < previous.value:
        return _change(previous, current, False, None, "counter_decreased")
    return _change(previous, current, True, current.value - previous.value, "comparable")


def _change(
    previous: ProviderMetricSnapshot,
    current: ProviderMetricSnapshot,
    comparable: bool,
    change: int | None,
    reason: Literal[
        "comparable",
        "identity_mismatch",
        "definition_mismatch",
        "unavailable",
        "counter_decreased",
        "observation_order_invalid",
    ],
) -> ProviderMetricChange:
    return ProviderMetricChange(
        previous_snapshot_id=previous.snapshot_id,
        current_snapshot_id=current.snapshot_id,
        comparable=comparable,
        change=change,
        reason=reason,
    )


__all__ = [
    "ProviderMetricConflictError",
    "ProviderMetricRepository",
    "compare_provider_metrics",
]
