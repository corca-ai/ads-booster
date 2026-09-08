"""Canonical remote operations with fenced leases and no reassignment after possible effects."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureJob,
)
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureLease as RemoteCaptureLease,  # noqa: PLC0414 - public compatibility export.
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_LEASE_SECONDS = 60
_MAX_QUEUE = 100
_SHA_LENGTH = 64


class RemoteCaptureRecord(ContractModel):
    job: RemoteCaptureJob
    state: Literal["queued", "ready", "leased", "started", "uncertain", "completed"]
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    canonical_settled: bool = False
    result: ToolExecutionResult | None = None
    submission_sha256: str | None = None

    @property
    def job_sha256(self) -> str:
        return contract_sha256(self.job)


class RemoteCaptureStore:
    def __init__(self, database: Path) -> None:
        self.database: Path = database
        with self._connect() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS remote_capture_jobs (
                tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                state TEXT NOT NULL, data TEXT NOT NULL,
                canonical_settled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(tenant_id,operation_id))""")
            columns = cast(
                "list[tuple[int, str, str, int, str | None, int]]",
                db.execute("PRAGMA table_info(remote_capture_jobs)").fetchall(),
            )
            if not any(column[1] == "canonical_settled" for column in columns):
                _ = db.execute("""ALTER TABLE remote_capture_jobs ADD COLUMN
                    canonical_settled INTEGER NOT NULL DEFAULT 0""")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=3)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _now(now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("remote_capture_time_requires_utc")

    @staticmethod
    def _get(
        db: sqlite3.Connection, tenant_id: str, operation_id: str
    ) -> RemoteCaptureRecord | None:
        row = cast(
            "tuple[str] | None",
            db.execute(
                "SELECT data FROM remote_capture_jobs WHERE tenant_id=? AND operation_id=?",
                (tenant_id, operation_id),
            ).fetchone(),
        )
        return RemoteCaptureRecord.model_validate_json(row[0]) if row else None

    @staticmethod
    def _put(db: sqlite3.Connection, record: RemoteCaptureRecord) -> None:
        _ = db.execute(
            """UPDATE remote_capture_jobs SET state=?,data=?,canonical_settled=?
            WHERE tenant_id=? AND operation_id=?""",
            (
                record.state,
                record.model_dump_json(),
                int(record.canonical_settled),
                record.job.profile.tenant_id,
                record.job.operation_id,
            ),
        )

    def get(self, tenant_id: str, operation_id: str) -> RemoteCaptureRecord | None:
        with self._connect() as db:
            return self._get(db, tenant_id, operation_id)

    def list_for_worker(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        states: tuple[str, ...],
        limit: int = 32,
        unsettled_only: bool = False,
    ) -> tuple[RemoteCaptureRecord, ...]:
        if (
            not 1 <= limit <= _MAX_QUEUE
            or not states
            or not set(states)
            <= {
                "queued",
                "ready",
                "leased",
                "started",
                "uncertain",
                "completed",
            }
        ):
            raise ValueError("remote_capture_list_filter_invalid")
        with self._connect() as db:
            rows = cast(
                "list[tuple[str]]",
                db.execute(
                    """SELECT data FROM remote_capture_jobs WHERE tenant_id=? AND worker_id=?
                    AND state IN (SELECT value FROM json_each(?))
                    AND (?=0 OR canonical_settled=0) ORDER BY operation_id LIMIT ?""",
                    (tenant_id, worker_id, json.dumps(states), int(unsettled_only), limit),
                ).fetchall(),
            )
            return tuple(RemoteCaptureRecord.model_validate_json(row[0]) for row in rows)

    def enqueue(self, job: RemoteCaptureJob, *, now: datetime) -> RemoteCaptureRecord:
        self._now(now)
        # Revalidate even callers using model_copy; this is a persisted trust boundary.
        job = RemoteCaptureJob.model_validate_json(job.model_dump_json())
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = self._get(db, job.profile.tenant_id, job.operation_id)
            if existing is not None:
                if existing.job != job:
                    raise ValueError("remote_capture_enqueue_conflict")
                return existing
            count = cast(
                "tuple[int]",
                db.execute(
                    """SELECT COUNT(*) FROM remote_capture_jobs WHERE tenant_id=? AND worker_id=?
                    AND state!='completed'""",
                    (job.profile.tenant_id, job.profile.worker_id),
                ).fetchone(),
            )[0]
            if count >= _MAX_QUEUE:
                raise ValueError("remote_capture_worker_queue_limit")
            record = RemoteCaptureRecord(job=job, state="queued")
            _ = db.execute(
                """INSERT INTO remote_capture_jobs
                (tenant_id,operation_id,worker_id,state,data) VALUES(?,?,?,?,?)""",
                (
                    job.profile.tenant_id,
                    job.operation_id,
                    job.profile.worker_id,
                    record.state,
                    record.model_dump_json(),
                ),
            )
            return record

    def arm(
        self, tenant_id: str, operation_id: str, *, job_sha256: str, now: datetime
    ) -> RemoteCaptureRecord:
        """Trusted coordinator calls only after canonical deferred acknowledgement is persisted."""
        self._now(now)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            record = self._require(db, tenant_id, operation_id, job_sha256)
            if record.state == "queued":
                record = record.model_copy(update={"state": "ready"})
                self._put(db, record)
            return record

    def claim(self, tenant_id: str, worker_id: str, *, now: datetime) -> RemoteCaptureLease | None:
        self._now(now)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            rows = cast(
                "list[tuple[str]]",
                db.execute(
                    """SELECT data FROM remote_capture_jobs WHERE tenant_id=? AND worker_id=?
                    AND state IN ('ready','leased','started','uncertain')
                    ORDER BY operation_id LIMIT 101""",
                    (tenant_id, worker_id),
                ).fetchall(),
            )
            records = [RemoteCaptureRecord.model_validate_json(row[0]) for row in rows]
            if any(
                record.state in {"started", "uncertain"}
                or (
                    record.state == "leased"
                    and record.lease_expires_at is not None
                    and record.lease_expires_at > now
                )
                for record in records
            ):
                return None
            if len(records) > _MAX_QUEUE:
                raise ValueError("remote_capture_worker_queue_limit")
            if not records:
                return None
            record = records[0]
            lease = RemoteCaptureLease(
                job=record.job,
                lease_id=secrets.token_hex(24),
                expires_at=now + timedelta(seconds=_LEASE_SECONDS),
            )
            self._put(
                db,
                record.model_copy(
                    update={
                        "state": "leased",
                        "lease_id": lease.lease_id,
                        "lease_expires_at": lease.expires_at,
                    }
                ),
            )
            return lease

    def start(
        self, tenant_id: str, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> RemoteCaptureRecord:
        self._now(now)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            record = self._require(db, tenant_id, operation_id, job_sha256, lease_id)
            if (
                record.state != "leased"
                or record.lease_expires_at is None
                or now >= record.lease_expires_at
            ):
                raise ValueError("remote_capture_start_not_dispatchable")
            record = record.model_copy(update={"state": "started"})
            self._put(db, record)
            return record

    def mark_uncertain(
        self, tenant_id: str, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> RemoteCaptureRecord:
        self._now(now)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            record = self._require(db, tenant_id, operation_id, job_sha256, lease_id)
            if record.state not in {"leased", "started", "uncertain"}:
                raise ValueError("remote_capture_uncertainty_invalid")
            record = record.model_copy(update={"state": "uncertain"})
            self._put(db, record)
            return record

    def complete(  # noqa: PLR0913 - exact lease, job and terminal submission binding.
        self,
        tenant_id: str,
        operation_id: str,
        *,
        lease_id: str,
        job_sha256: str,
        result: ToolExecutionResult,
        submission_sha256: str,
        now: datetime,
    ) -> RemoteCaptureRecord:
        self._now(now)
        result = ToolExecutionResult.model_validate_json(result.model_dump_json())
        if len(submission_sha256) != _SHA_LENGTH or any(
            c not in "0123456789abcdef" for c in submission_sha256
        ):
            raise ValueError("remote_capture_completion_digest_invalid")
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            record = self._require(db, tenant_id, operation_id, job_sha256, lease_id)
            if result.invocation_sha256 != contract_sha256(record.job.invocation):
                raise ValueError("remote_capture_completion_invocation_mismatch")
            if record.state == "completed":
                if record.result != result or record.submission_sha256 != submission_sha256:
                    raise ValueError("remote_capture_completion_conflict")
                return record
            # Lease expiry fences permission to START, not terminal cleanup. An exact
            # still-leased job has never crossed the server start barrier or been reassigned.
            # _require above rejects an old lease after reassignment before this exemption.
            allowed = (
                record.state == "leased"
                if result.disposition == "no_effect"
                else record.state in {"started", "uncertain"}
            )
            if not allowed:
                raise ValueError("remote_capture_completion_not_started")
            record = record.model_copy(
                update={
                    "state": "completed",
                    "result": result,
                    "submission_sha256": submission_sha256,
                }
            )
            self._put(db, record)
            return record

    def acknowledge_completion(
        self,
        tenant_id: str,
        operation_id: str,
        *,
        job_sha256: str,
        result_sha256: str,
        now: datetime,
    ) -> RemoteCaptureRecord:
        """Mark a local canonical receipt projection only after exact terminal settlement."""
        self._now(now)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            record = self._require(db, tenant_id, operation_id, job_sha256)
            if (
                record.state != "completed"
                or record.result is None
                or contract_sha256(record.result) != result_sha256
            ):
                raise ValueError("remote_capture_settlement_binding_invalid")
            record = record.model_copy(update={"canonical_settled": True})
            self._put(db, record)
            return record

    @staticmethod
    def _require(
        db: sqlite3.Connection,
        tenant_id: str,
        operation_id: str,
        job_sha256: str,
        lease_id: str | None = None,
    ) -> RemoteCaptureRecord:
        record = RemoteCaptureStore._get(db, tenant_id, operation_id)
        if (
            record is None
            or record.job_sha256 != job_sha256
            or (lease_id is not None and record.lease_id != lease_id)
        ):
            raise ValueError("remote_capture_binding_invalid")
        return record
