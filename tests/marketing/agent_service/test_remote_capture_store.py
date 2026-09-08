"""Only acknowledged jobs lease; exact fences survive restarts and ambiguous worker starts."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.marketing.agent_service.remote_capture_store import (
    RemoteCaptureRecord,
    RemoteCaptureStore,
)
from tests.marketing.agent_service.test_creative_capture import NOW
from tests.marketing.agent_service.test_remote_capture_contract import job

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.agent_service.remote_capture_contract import (
        RemoteCaptureJob,
        RemoteCaptureLease,
    )


def queued(tmp_path: Path) -> tuple[RemoteCaptureStore, RemoteCaptureJob]:
    original = job(tmp_path)
    store = RemoteCaptureStore(tmp_path / "queue.db")
    assert store.enqueue(original, now=NOW) == store.enqueue(original, now=NOW)
    return store, original


def ready(tmp_path: Path) -> tuple[RemoteCaptureStore, RemoteCaptureLease]:
    store, original = queued(tmp_path)
    _ = store.arm("tenant-a", original.operation_id, job_sha256=contract_sha256(original), now=NOW)
    lease = store.claim("tenant-a", "mac", now=NOW)
    assert lease is not None
    return store, lease


def result(original: RemoteCaptureJob, disposition: str = "succeeded") -> ToolExecutionResult:
    return ToolExecutionResult.model_validate(
        {
            "schema_version": "trace.tool-execution-result.v1",
            "disposition": disposition,
            "invocation_sha256": contract_sha256(original.invocation),
            "output": {"synthetic": True},
            "actual_cost_units": 1,
            "executor_id": "mac",
        }
    )


def test_unarmed_scope_and_exact_enqueue_conflict(tmp_path: Path) -> None:
    store, original = queued(tmp_path)
    assert store.claim("tenant-a", "mac", now=NOW) is None
    assert store.get("other", original.operation_id) is None
    assert store.list_for_worker("tenant-a", "other", states=("queued",)) == ()
    assert len(store.list_for_worker("tenant-a", "mac", states=("queued",))) == 1
    changed = original.model_copy(
        update={"approval": original.approval.model_copy(update={"approver_id": "other"})}
    )
    with pytest.raises(ValueError, match="enqueue_conflict"):
        _ = store.enqueue(changed, now=NOW)


def test_expired_unstarted_lease_fenced_then_started_never_reassigned(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    assert store.claim("tenant-a", "mac", now=NOW) is None
    later = NOW + timedelta(seconds=61)
    next_lease = store.claim("tenant-a", "mac", now=later)
    assert next_lease is not None
    assert next_lease.lease_id != lease.lease_id
    with pytest.raises(ValueError, match="binding_invalid"):
        _ = store.start(
            "tenant-a",
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            now=later,
        )
    started = store.start(
        "tenant-a",
        lease.job.operation_id,
        lease_id=next_lease.lease_id,
        job_sha256=lease.job_sha256,
        now=later,
    )
    assert started.state == "started"
    assert (
        RemoteCaptureStore(store.database).claim("tenant-a", "mac", now=later + timedelta(days=1))
        is None
    )
    with pytest.raises(ValueError, match="start_not_dispatchable"):
        _ = store.start(
            "tenant-a",
            lease.job.operation_id,
            lease_id=next_lease.lease_id,
            job_sha256=lease.job_sha256,
            now=later,
        )


def test_lost_start_response_and_terminal_replay(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    late = NOW + timedelta(seconds=80)
    uncertain = store.mark_uncertain(
        "tenant-a",
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        now=late,
    )
    assert uncertain.state == "uncertain"
    assert store.claim("tenant-a", "mac", now=late) is None
    terminal = result(lease.job)
    completed = store.complete(
        "tenant-a",
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        result=terminal,
        submission_sha256="c" * 64,
        now=late,
    )
    assert completed.state == "completed"
    assert (
        store.complete(
            "tenant-a",
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            result=terminal,
            submission_sha256="c" * 64,
            now=late,
        )
        == completed
    )
    with pytest.raises(ValueError, match="completion_conflict"):
        _ = store.complete(
            "tenant-a",
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            result=terminal,
            submission_sha256="d" * 64,
            now=late,
        )


def test_prestart_only_exact_unexpired_no_effect(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    with pytest.raises(ValueError, match="completion_not_started"):
        _ = store.complete(
            "tenant-a",
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            result=result(lease.job),
            submission_sha256="c" * 64,
            now=NOW,
        )
    completed = store.complete(
        "tenant-a",
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        result=result(lease.job, "no_effect"),
        submission_sha256="c" * 64,
        now=NOW,
    )
    assert completed.state == "completed"


@pytest.mark.parametrize(
    ("field", "value"), [("tenant", "other"), ("lease", "wrong"), ("digest", "f" * 64)]
)
def test_start_scope_and_fences_fail_closed(tmp_path: Path, field: str, value: str) -> None:
    store, lease = ready(tmp_path)
    with pytest.raises(ValueError, match="binding_invalid"):
        _ = store.start(
            value if field == "tenant" else "tenant-a",
            lease.job.operation_id,
            lease_id=value if field == "lease" else lease.lease_id,
            job_sha256=value if field == "digest" else lease.job_sha256,
            now=NOW,
        )
    record = store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.state == "leased"


def test_expired_same_lease_no_effect_settles_without_start_authority(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    terminal = result(lease.job, "no_effect").model_copy(update={"actual_cost_units": 0})
    completed = store.complete(
        "tenant-a",
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        result=terminal,
        submission_sha256="c" * 64,
        now=NOW + timedelta(seconds=61),
    )
    assert completed.state == "completed"
    assert completed.result is not None
    assert completed.result.actual_cost_units == 0


def test_expired_old_lease_no_effect_denied_after_reassignment(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    late = NOW + timedelta(seconds=61)
    replacement = store.claim("tenant-a", "mac", now=late)
    assert replacement is not None
    assert replacement.lease_id != lease.lease_id
    with pytest.raises(ValueError, match="binding_invalid"):
        _ = store.complete(
            "tenant-a",
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            result=result(lease.job, "no_effect"),
            submission_sha256="c" * 64,
            now=late,
        )


def test_settlement_marker_filters_before_limit_and_requires_exact_result(tmp_path: Path) -> None:
    store, lease = ready(tmp_path)
    completed = store.complete(
        "tenant-a",
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        result=result(lease.job, "no_effect"),
        submission_sha256="c" * 64,
        now=NOW,
    )
    assert not completed.canonical_settled
    assert (
        len(
            store.list_for_worker(
                "tenant-a", "mac", states=("completed",), unsettled_only=True, limit=1
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="settlement_binding_invalid"):
        _ = store.acknowledge_completion(
            "tenant-a",
            lease.job.operation_id,
            job_sha256=lease.job_sha256,
            result_sha256="f" * 64,
            now=NOW,
        )
    assert completed.result is not None
    settled = store.acknowledge_completion(
        "tenant-a",
        lease.job.operation_id,
        job_sha256=lease.job_sha256,
        result_sha256=contract_sha256(completed.result),
        now=NOW,
    )
    assert settled.canonical_settled
    assert (
        store.list_for_worker(
            "tenant-a", "mac", states=("completed",), unsettled_only=True, limit=1
        )
        == ()
    )
    assert len(store.list_for_worker("tenant-a", "mac", states=("completed",), limit=1)) == 1
    assert RemoteCaptureStore(store.database).get("tenant-a", lease.job.operation_id) == settled


def test_existing_queue_schema_migrates_settlement_without_losing_job(tmp_path: Path) -> None:
    original = job(tmp_path)
    database = tmp_path / "legacy-queue.db"
    with closing(sqlite3.connect(database)) as db, db:
        _ = db.execute("""CREATE TABLE remote_capture_jobs (tenant_id TEXT,operation_id TEXT,
            worker_id TEXT,state TEXT,data TEXT,PRIMARY KEY(tenant_id,operation_id))""")
        payload = RemoteCaptureRecord(job=original, state="queued").model_dump_json(
            exclude={"canonical_settled"}
        )
        _ = db.execute(
            "INSERT INTO remote_capture_jobs VALUES(?,?,?,?,?)",
            ("tenant-a", original.operation_id, "mac", "queued", payload),
        )
    store = RemoteCaptureStore(database)
    current = store.get("tenant-a", original.operation_id)
    assert current is not None
    assert current.job == original
    assert not current.canonical_settled
    assert (
        len(store.list_for_worker("tenant-a", "mac", states=("queued",), unsettled_only=True)) == 1
    )
