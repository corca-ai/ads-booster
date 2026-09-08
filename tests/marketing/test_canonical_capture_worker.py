"""No live calls: fake control plane and existing fake native worker exercise crash order."""

from __future__ import annotations

import base64
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureJob,
    RemoteCaptureLease,
)
from ads_booster.marketing.canonical_capture_worker import CanonicalCaptureWorker, RemoteCaptureHttp
from tests.marketing.agent_service.test_creative_capture import NOW, FakeWorker, png
from tests.marketing.agent_service.test_remote_capture_contract import job
from tests.marketing.agent_service.test_slack_image_review import Response

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class Crash(BaseException):
    pass


class Plane:
    def __init__(self, lease: RemoteCaptureLease) -> None:
        self.lease: RemoteCaptureLease = lease
        self.calls: list[str] = []
        self.uploads: list[JsonObject] = []
        self.fail_start: bool = False
        self.crash_start: bool = False
        self.fail_upload: bool = False
        self.bad_source: bool = False
        self.claimed: bool = False
        self.profile_changed: bool = False
        self.root: Path | None = None
        self.reject_start: bool = False
        self.oversized_source: bool = False
        self.terminal_status: bool = False
        self.wrong_status: bool = False

    def request(  # noqa: C901, PLR0911, PLR0912 - fake routes mirror each worker protocol boundary.
        self, method: str, path: str, body: JsonObject | None = None
    ) -> JsonObject:
        self.calls.append(f"{method} {path}")
        if path.endswith("/profile"):
            profile = self.lease.job.profile.model_dump(mode="json")
            if self.profile_changed:
                profile["worker_id"] = "different"
            return {"profile": profile}
        if path.endswith("/heartbeat"):
            return {"accepted": True}
        if path.endswith("/claim"):
            if self.claimed:
                return {"lease": None}
            self.claimed = True
            return {"lease": self.lease.model_dump(mode="json")}
        if "/status?" in path:
            return {
                "operation_id": self.lease.job.operation_id,
                "lease_id": "wrong" if self.wrong_status else self.lease.lease_id,
                "job_sha256": contract_sha256(self.lease.job),
                "state": "completed" if self.terminal_status else "uncertain",
                "canonical_settled": self.terminal_status,
            }
        if "/source?" in path:
            if self.oversized_source:
                return {
                    "sha256": self.lease.job.source.sha256,
                    "image_base64": "a" * (14 * 1024 * 1024 + 1),
                }
            return {
                "sha256": self.lease.job.source.sha256,
                "image_base64": base64.b64encode(b"bad" if self.bad_source else png()).decode(),
            }
        if path.endswith("/start"):
            assert self.root is not None
            with closing(sqlite3.connect(self.root / "worker.sqlite3")) as db:
                assert db.execute("SELECT stage FROM captures").fetchone() == ("start_intent",)
            if self.reject_start:
                return {"started": False, "accepted": True}
            if self.crash_start:
                raise Crash
            if self.fail_start:
                message = "lost start response"
                raise OSError(message)
            return {"started": True}
        if path.endswith("/complete"):
            assert body is not None
            self.uploads.append(body)
            if self.fail_upload:
                self.fail_upload = False
                message = "lost completion response"
                raise OSError(message)
            return {"accepted": True}
        assert path.endswith("/uncertain")
        return {"accepted": True}


def setup(tmp_path: Path) -> tuple[CanonicalCaptureWorker, Plane, FakeWorker]:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    initial = job(fixture)
    values = initial.model_dump(mode="json")
    values["profile"]["python_executable"] = sys.executable
    values["contract"]["python_executable"] = sys.executable
    actual = RemoteCaptureJob.model_validate(values)
    lease = RemoteCaptureLease(
        job=actual, lease_id="lease-1", expires_at=NOW + timedelta(minutes=1)
    )
    plane, native = Plane(lease), FakeWorker()
    worker = CanonicalCaptureWorker(
        root=tmp_path / "worker",
        profile=actual.profile,
        http=plane,
        worker=native,
        probe=lambda now: ToolReadiness(ready=True, observed_at=now, max_age_seconds=60),
        clock=lambda: NOW,
    )
    plane.root = worker.root
    return worker, plane, native


def test_prepare_start_execute_upload_order_and_same_root_replay(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    assert worker.work_once()["state"] == "done"
    assert native.calls == 1
    assert plane.calls[3].startswith("GET /workers/capture/jobs/")
    assert plane.calls[4] == "POST /workers/capture/start"
    assert plane.calls[5] == "POST /workers/capture/complete"
    assert worker.work_once()["state"] == "idle"
    assert native.calls == 1
    upload = plane.uploads[0]["upload"]
    assert isinstance(upload, dict)
    assert upload["job_sha256"] == contract_sha256(plane.lease.job)
    assert upload["provenance"] is not None


@pytest.mark.parametrize("crash", [False, True])
def test_lost_start_or_process_crash_never_executes_on_restart(tmp_path: Path, crash: bool) -> None:
    worker, plane, native = setup(tmp_path)
    if crash:
        plane.crash_start = True
        with pytest.raises(Crash):
            _ = worker.work_once()
    else:
        plane.fail_start = True
        assert worker.work_once()["state"] == "uncertain"
    restarted = replace(worker)
    assert restarted.work_once()["state"] == "uncertain"
    assert native.calls == 0
    assert plane.calls.count("POST /workers/capture/start") == 1


def test_lost_completion_response_only_retries_identical_upload(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    plane.fail_upload = True
    assert worker.work_once()["state"] == "upload_pending"
    restarted = replace(worker)
    assert restarted.work_once()["state"] == "done"
    assert len(plane.uploads) == 2
    assert plane.uploads[0] == plane.uploads[1]
    assert native.calls == 1
    assert plane.calls.count("POST /workers/capture/start") == 1


def test_source_decode_failure_has_no_start_or_device_effect(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    plane.bad_source = True
    assert worker.work_once()["state"] == "done"
    assert native.calls == 0
    assert "POST /workers/capture/start" not in plane.calls
    upload = plane.uploads[0]["upload"]
    assert isinstance(upload, dict)
    assert upload["disposition"] == "no_effect"


def test_doctor_is_local_readonly_and_changed_server_profile_fails_before_claim(
    tmp_path: Path,
) -> None:
    worker, plane, native = setup(tmp_path)
    assert worker.doctor()["ready"] is True
    assert not worker.root.exists()
    assert plane.calls == []
    plane.profile_changed = True
    with pytest.raises(ValueError, match="remote_capture_profile_changed"):
        _ = worker.work_once()
    assert plane.calls == ["GET /workers/capture/profile"]
    assert native.calls == 0


def test_python_pin_mismatch_is_unavailable_without_device(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    changed = replace(
        worker,
        profile=worker.profile.model_copy(update={"python_executable": "/different/mac/python"}),
    )
    assert changed.doctor()["reason_code"] == "capture_python_profile_mismatch"
    assert plane.calls == []
    assert native.calls == 0


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "file:///tmp/example",
    ],
)
def test_transport_rejects_non_pinned_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="remote_capture_origin_invalid"):
        _ = RemoteCaptureHttp(origin, "TOKEN")


def test_rejected_start_does_not_prepare_device_or_retry(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    plane.reject_start = True
    assert worker.work_once()["state"] == "not_started"
    assert replace(worker).work_once()["state"] == "idle"
    assert native.calls == 0


def test_source_size_cap_precedes_device_start(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    plane.oversized_source = True
    assert worker.work_once()["state"] == "done"
    assert native.calls == 0
    assert "POST /workers/capture/start" not in plane.calls


def test_stale_probe_cannot_claim_readiness(tmp_path: Path) -> None:
    worker, _, _ = setup(tmp_path)

    def stale_probe(now: datetime) -> ToolReadiness:
        return ToolReadiness(ready=True, observed_at=now - timedelta(minutes=2), max_age_seconds=60)

    stale = replace(worker, probe=stale_probe)
    assert stale.doctor()["ready"] is False
    assert stale.doctor()["reason_code"] == "capture_readiness_stale"


def test_http_redirect_error_never_exposes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_CAPTURE_TEST_TOKEN", "synthetic-hidden-token")
    response = Response(b"{}", "https://other.example/private")
    http = RemoteCaptureHttp(
        "https://capture.example",
        "TRACE_CAPTURE_TEST_TOKEN",
        opener=lambda *args, **kwargs: response,
    )
    with pytest.raises(ValueError, match=r"^remote_capture_http_failed$") as error:
        _ = http.request("GET", "/workers/capture/profile")
    assert response.closed
    assert "synthetic-hidden-token" not in str(error.value)
    assert "synthetic-hidden-token" not in repr(http)


def test_expired_approval_reports_no_effect_without_starting_device(tmp_path: Path) -> None:
    worker, plane, native = setup(tmp_path)
    expired_at = plane.lease.job.approval.expires_at
    assert expired_at is not None
    now = expired_at + timedelta(seconds=1)
    plane.lease = plane.lease.model_copy(update={"expires_at": now + timedelta(minutes=1)})
    worker = replace(worker, clock=lambda: now)
    assert worker.work_once()["state"] == "done"
    assert native.calls == 0
    assert "POST /workers/capture/start" not in plane.calls
    assert not any("/source?" in call for call in plane.calls)
    upload = plane.uploads[0]["upload"]
    assert isinstance(upload, dict)
    assert upload["disposition"] == "no_effect"
    assert upload["error_code"] == "capture_approval_expired"
    assert worker.work_once()["state"] == "idle"


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_lost_start_recovers_only_exact_settled_server_terminal(
    tmp_path: Path, wrong_identity: bool
) -> None:
    worker, plane, native = setup(tmp_path)
    plane.crash_start = True
    with pytest.raises(Crash):
        _ = worker.work_once()
    plane.terminal_status = True
    plane.wrong_status = wrong_identity
    assert replace(worker).work_once()["state"] == ("uncertain" if wrong_identity else "done")
    assert native.calls == 0
    assert plane.calls.count("POST /workers/capture/start") == 1
    if not wrong_identity:
        assert worker.work_once()["state"] == "idle"


@pytest.mark.parametrize("directory", ["inputs", "outputs"])
def test_preexisting_job_directory_symlink_cannot_escape_worker_root(
    tmp_path: Path, directory: str
) -> None:
    worker, plane, native = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    job_root = worker.root / contract_sha256(
        {"operation": plane.lease.job.operation_id, "lease": plane.lease.lease_id}
    )
    job_root.mkdir(parents=True)
    (job_root / directory).symlink_to(outside, target_is_directory=True)
    assert worker.work_once()["state"] == "done"
    assert native.calls == 0
    assert "POST /workers/capture/start" not in plane.calls
    assert not tuple(outside.iterdir())
