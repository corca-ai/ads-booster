# pyright: reportPrivateUsage=false
"""Worker routes have an isolated token boundary and never inherit browser/Slack authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    AgentRunState,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, DeviceKind, DeviceTarget
from ads_booster.marketing.agent_service.maintenance import MaintenanceGate
from ads_booster.marketing.agent_service.remote_capture_api import (
    MAX_CAPTURE_UPLOAD_BYTES,
    capture_body_limit,
)
from ads_booster.marketing.agent_service.remote_capture_contract import RemoteCaptureProfile
from tests.marketing.agent_service.test_http_api import _api
from tests.marketing.agent_service.test_remote_capture import approved, setup, upload

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.agent_service.remote_capture_contract import RemoteCaptureUpload
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


@dataclass
class Owner:
    calls: list[str] = field(default_factory=list)
    fail: bool = False
    profile: RemoteCaptureProfile = field(
        default_factory=lambda: RemoteCaptureProfile(
            worker_id="mac",
            tenant_id="team",
            device=DeviceTarget(
                kind=DeviceKind.SIMULATOR,
                udid="A" * 36,
                platform_version="18.0",
                device_name="fixture",
            ),
            python_executable="/fixture/python",
            appium_server="http://127.0.0.1:4723",
        )
    )

    def authenticate(self, authorization: str | None) -> bool:
        return authorization == "Bearer worker-only"

    def heartbeat(
        self, *, profile_sha256: str, ready: bool, reason_code: str | None, now: datetime
    ) -> None:
        _ = profile_sha256, ready, reason_code, now
        self.calls.append("heartbeat")

    def claim(self, *, now: datetime) -> ContractModel | None:
        _ = now
        if self.fail:
            message = "secret-token internal /private/location"
            raise ValueError(message)
        self.calls.append("claim")
        return None

    def start(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> JsonObject:
        _ = operation_id, lease_id, job_sha256, now
        self.calls.append("start")
        return {"started": True}

    def source(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject:
        _ = now
        assert operation_id == "operation"
        assert lease_id == "lease"
        self.calls.append("source")
        return {"image_base64": "fixture", "sha256": "a" * 64}

    def status(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject:
        _ = now
        self.calls.append("status")
        return {
            "operation_id": operation_id,
            "lease_id": lease_id,
            "state": "completed",
            "canonical_settled": True,
        }

    def complete(
        self, operation_id: str, *, upload: RemoteCaptureUpload, now: datetime
    ) -> AgentRun:
        _ = operation_id, upload
        self.calls.append("complete")
        return result_run(now)

    def uncertain(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> AgentRun:
        _ = operation_id, lease_id, job_sha256
        self.calls.append("uncertain")
        return result_run(now)


def result_run(now: datetime) -> AgentRun:
    return AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="work",
        tenant_id="team",
        goal=AgentGoal(objective="fixture capture", success_criteria=("proof",)),
        budget=AgentBudget(max_tool_calls=1, max_cost_units=1),
        state=AgentRunState.AWAITING_RECONCILIATION,
        created_at=now,
        updated_at=now,
    )


def test_default_off_and_worker_token_is_independent_in_slack_only(tmp_path: Path) -> None:
    owner = Owner()
    api = replace(_api(tmp_path), slack_only=True)
    assert (
        api.dispatch("GET", "/workers/capture/profile", authorization="Bearer worker-only").status
        == 404
    )
    api = replace(api, remote_capture=owner)
    for token in (None, "Bearer oauth-token", "Bearer test-token"):
        assert (
            api.dispatch("POST", "/workers/capture/claim", authorization=token, body=b"{}").status
            == 401
        )
    assert owner.calls == []
    assert (
        api.dispatch("GET", "/workers/capture/profile", authorization="Bearer worker-only").status
        == 200
    )
    response = api.dispatch(
        "POST", "/workers/capture/claim", authorization="Bearer worker-only", body=b"{}"
    )
    assert response.body == {"lease": None}
    assert owner.calls == ["claim"]


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("heartbeat", {"profile_sha256": "a" * 64, "ready": False, "reason_code": "missing"}),
        ("start", {"operation_id": "operation", "lease_id": "lease", "job_sha256": "a" * 64}),
        ("uncertain", {"operation_id": "operation", "lease_id": "lease", "job_sha256": "a" * 64}),
        (
            "complete",
            {
                "operation_id": "operation",
                "upload": {
                    "job_sha256": "a" * 64,
                    "lease_id": "lease",
                    "disposition": "no_effect",
                    "error_code": "cancelled",
                },
            },
        ),
    ],
)
def test_fixed_worker_routes_validate_and_delegate(
    tmp_path: Path, route: str, body: JsonObject
) -> None:
    owner = Owner()
    api = replace(_api(tmp_path), remote_capture=owner)
    response = api.dispatch(
        "POST",
        "/workers/capture/" + route,
        authorization="Bearer worker-only",
        body=json.dumps(body).encode(),
        now=NOW,
    )
    assert response.status == 200
    assert owner.calls == [route]
    body["tenant_id"] = "other"
    assert (
        api.dispatch(
            "POST",
            "/workers/capture/" + route,
            authorization="Bearer worker-only",
            body=json.dumps(body).encode(),
            now=NOW,
        ).status
        == 400
    )
    assert owner.calls == [route]


def test_source_requires_single_lease_and_errors_are_sanitized(tmp_path: Path) -> None:
    owner = Owner()
    api = replace(_api(tmp_path), remote_capture=owner)
    target = "/workers/capture/jobs/operation/source"
    for query in (
        "",
        "?lease_id=lease&lease_id=other",
        "?url=https://other",
        "?lease_id=../secret",
    ):
        assert api.dispatch("GET", target + query, authorization="Bearer worker-only").status in {
            400,
            409,
        }
    assert owner.calls == []
    assert (
        api.dispatch("GET", target + "?lease_id=lease", authorization="Bearer worker-only").status
        == 200
    )
    owner.fail = True
    response = api.dispatch(
        "POST", "/workers/capture/claim", authorization="Bearer worker-only", body=b"{}"
    )
    assert response.status == 409
    assert "secret" not in str(response.body)


def test_large_body_allowance_is_exact_authenticated_completion_only(tmp_path: Path) -> None:
    owner = Owner()
    assert capture_body_limit("POST", "/v1/runs", owner, "Bearer worker-only") is None
    assert (
        capture_body_limit("POST", "/workers/capture/complete", owner, "Bearer worker-only")
        == MAX_CAPTURE_UPLOAD_BYTES
    )
    assert (
        capture_body_limit("POST", "/workers/capture/complete", owner, "Bearer operator") == 16384
    )
    api = replace(_api(tmp_path), remote_capture=owner)
    assert (
        api.dispatch(
            "POST", "/workers/capture/claim", authorization="Bearer worker-only", body=b" " * 16385
        ).status
        == 413
    )
    assert owner.calls == []


def test_actual_coordinator_heartbeat_and_claim_contract(tmp_path: Path) -> None:

    remote = tmp_path / "remote"
    remote.mkdir()
    owner = setup(remote)
    api = replace(_api(tmp_path), remote_capture=owner, slack_only=True)
    response = api.dispatch(
        "POST",
        "/workers/capture/heartbeat",
        authorization="Bearer " + "x" * 32,
        body=json.dumps({"profile_sha256": contract_sha256(owner.profile), "ready": True}).encode(),
        now=NOW,
    )
    assert response.status == 200
    response = api.dispatch(
        "POST", "/workers/capture/claim", authorization="Bearer " + "x" * 32, body=b"{}", now=NOW
    )
    assert response.status == 200
    assert response.body == {"lease": None}


def test_maintenance_blocks_worker_before_owner(tmp_path: Path) -> None:

    marker = tmp_path / "maintenance"
    marker.touch()
    owner = Owner()
    api = replace(_api(tmp_path), remote_capture=owner, maintenance=MaintenanceGate(marker))
    assert (
        api.dispatch(
            "POST", "/workers/capture/claim", authorization="Bearer worker-only", body=b"{}"
        ).status
        == 503
    )
    assert owner.calls == []


def test_actual_coordinator_source_start_complete_routes(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    owner, lease = approved(remote)
    api = replace(_api(tmp_path), remote_capture=owner, slack_only=True)
    authorization = "Bearer " + "x" * 32
    operation = lease.job.operation_id
    source = api.dispatch(
        "GET",
        f"/workers/capture/jobs/{operation}/source?lease_id={lease.lease_id}",
        authorization=authorization,
        now=NOW,
    )
    assert source.status == 200
    start = api.dispatch(
        "POST",
        "/workers/capture/start",
        authorization=authorization,
        now=NOW,
        body=json.dumps(
            {"operation_id": operation, "lease_id": lease.lease_id, "job_sha256": lease.job_sha256}
        ).encode(),
    )
    assert start.status == 200
    assert start.body == {"started": True}
    completion = api.dispatch(
        "POST",
        "/workers/capture/complete",
        authorization=authorization,
        now=NOW,
        body=json.dumps(
            {"operation_id": operation, "upload": upload(lease).model_dump(mode="json")}
        ).encode(),
    )
    assert completion.status == 200
    assert completion.body == {"accepted": True, "run_id": "run-one", "state": "completed"}


def test_status_uses_same_authenticated_lease_boundary(tmp_path: Path) -> None:
    owner = Owner()
    api = replace(_api(tmp_path), remote_capture=owner)
    path = "/workers/capture/jobs/operation/status?lease_id=lease"
    assert api.dispatch("GET", path, authorization=None).status == 401
    assert owner.calls == []
    response = api.dispatch("GET", path, authorization="Bearer worker-only", now=NOW)
    assert response.status == 200
    assert owner.calls == ["status"]
    assert (
        api.dispatch(
            "GET", path + "&lease_id=other", authorization="Bearer worker-only", now=NOW
        ).status
        == 400
    )
    assert owner.calls == ["status"]
