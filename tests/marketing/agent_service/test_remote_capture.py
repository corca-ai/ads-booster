# pyright: reportPrivateUsage=false
"""Real canonical remote lifecycle with synthetic images; no worker device or HTTP effects."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.models import CaptureProvenance
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.remote_capture import (
    RemoteCaptureCatalog,
    RemoteCaptureConfig,
    RemoteCaptureCoordinator,
)
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureLease,
    RemoteCaptureUpload,
)
from ads_booster.marketing.agent_service.remote_capture_store import RemoteCaptureStore
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.agent_service.work_continuation import continue_work
from ads_booster.marketing.runtime import SqliteSessionStore
from tests.marketing.agent_service.test_application import _reasoning_result, _request
from tests.marketing.agent_service.test_creative_capture import NOW, png
from tests.marketing.agent_service.test_remote_capture_contract import job

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class Reasoning:
    def __init__(self, payload: JsonObject) -> None:
        self.payload: JsonObject = payload

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        done = any(item.get("capability_id") == "capture.appium" for item in request.evidence)
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop" if done else "invoke_tool",
                capability_id=None if done else "capture.appium",
                tool_input=None if done else self.payload,
                reasoning_summary="Approved remote synthetic capture",
                expected_outcome="Same Run receives a verified file",
            ),
        )


def setup(tmp_path: Path) -> RemoteCaptureCoordinator:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    seed = job(fixture)
    database = tmp_path / "canonical.db"
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(()),
        reasoning=Reasoning(seed.invocation.input),
        tools={},
        runtime_store=SqliteSessionStore(database),
    )
    assets = SqliteCreativeAssetRepository(database, tmp_path / "artifacts")
    _ = (assets.artifact_root / seed.source.relative_path).write_bytes(png())
    assets.add(seed.source, actor_scope=seed.source.scope)
    coordinator = RemoteCaptureCoordinator(
        service,
        RemoteCaptureConfig(profile=seed.profile, token_sha256=sha256(b"x" * 32).hexdigest()),
        RemoteCaptureStore(database),
        assets,
        clock=lambda: NOW,
    )
    coordinator.heartbeat(
        profile_sha256=contract_sha256(seed.profile), ready=True, reason_code=None, now=NOW
    )
    catalog = RemoteCaptureCatalog(service.registry, coordinator)
    service.registry = ToolRegistry(catalog.descriptors(now=NOW), provider=catalog)
    service.tools = {"capture.appium": coordinator}
    request = _request().model_copy(
        update={"tenant_id": "tenant-a", "budget": AgentBudget(max_tool_calls=4, max_cost_units=40)}
    )
    assert service.create(request, now=NOW).state is AgentRunState.AWAITING_APPROVAL
    assert coordinator.claim(now=NOW) is None
    return coordinator


def approved(
    tmp_path: Path, *, expires_after: int = 300
) -> tuple[RemoteCaptureCoordinator, RemoteCaptureLease]:
    coordinator = setup(tmp_path)
    invocation = ToolInvocation.model_validate(
        next(
            item.payload
            for item in coordinator.service.repository.records("tenant-a", "run-one")
            if item.kind is AgentRecordKind.INVOCATION
        )
    )
    assert (
        coordinator.service.decide_approval(
            "tenant-a",
            "run-one",
            approver_id="reviewer",
            granted=True,
            expected_invocation_sha256=contract_sha256(invocation),
            now=NOW,
            expires_at=NOW + timedelta(seconds=expires_after),
        ).state
        is AgentRunState.AWAITING_TOOL
    )
    lease = coordinator.claim(now=NOW)
    assert lease is not None
    return coordinator, lease


def upload(lease: RemoteCaptureLease) -> RemoteCaptureUpload:
    contract = lease.job.contract
    provenance = CaptureProvenance(
        request_sha256=contract.request_sha256,
        artifact_sha256=sha256(png()).hexdigest(),
        bundle_id=contract.bundle_id,
        device_udid=contract.device.udid,
        session_id="fake-worker",
        byte_size=len(png()),
        width=32,
        height=64,
        source_modified_at_ns=1,
        native_export_nonce=contract.export_nonce,
        native_export_binding_verified=True,
    )
    return RemoteCaptureUpload(
        job_sha256=lease.job_sha256,
        lease_id=lease.lease_id,
        disposition="succeeded",
        provenance=provenance,
        png_base64=base64.b64encode(png()).decode(),
    )


def test_exact_approval_to_native_asset_and_same_run_receipt(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    source = coordinator.source(lease.job.operation_id, lease_id=lease.lease_id, now=NOW)
    assert source["sha256"] == lease.job.source.sha256
    assert (
        coordinator.start(
            lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
        )["started"]
        is True
    )
    outcome = coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW)
    assert outcome.state is AgentRunState.COMPLETED
    asset = coordinator.assets.get(lease.job.source.scope, lease.job.operation_id)
    assert asset is not None
    assert asset.origin == "worker_receipt"
    assert not asset.product_proof_verified
    assert coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW) == outcome
    assert len(coordinator.service.repository.list_runs("tenant-a")) == 1
    assert coordinator.claim(now=NOW) is None


@pytest.mark.parametrize("mode", ["before_start", "wrong_lease", "wrong_digest"])
def test_invalid_completion_never_registers_asset(tmp_path: Path, mode: str) -> None:
    coordinator, lease = approved(tmp_path)
    body = upload(lease)
    if mode == "wrong_lease":
        body = body.model_copy(update={"lease_id": "other"})
    elif mode == "wrong_digest":
        body = body.model_copy(update={"job_sha256": "f" * 64})
    with pytest.raises(ValueError, match=r"lease_invalid|not_started"):
        _ = coordinator.complete(lease.job.operation_id, upload=body, now=NOW)
    assert coordinator.assets.get(lease.job.source.scope, lease.job.operation_id) is None


def test_source_revision_change_before_start_produces_no_effect(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    coordinator.assets.add(
        lease.job.source.model_copy(update={"revision": 2}), actor_scope=lease.job.source.scope
    )
    started = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    assert started["started"] is False
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.result is not None
    assert record.result.disposition == "no_effect"
    assert record.result.actual_cost_units == 0


def test_lease_expiring_during_preparation_settles_without_start(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    later = NOW + timedelta(seconds=61)
    coordinator.heartbeat(
        profile_sha256=contract_sha256(coordinator.profile), ready=True, reason_code=None, now=later
    )
    response = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=later
    )
    assert response == {"started": False, "accepted": True}
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.result is not None
    assert record.result.disposition == "no_effect"
    assert record.result.output["reason_code"] == "remote_capture_lease_expired"
    assert record.canonical_settled


def test_completion_notification_gap_recovers_before_queue_settlement(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    notices: list[tuple[str, str, str]] = []

    def notify(tenant_id: str, run_id: str, event_id: str) -> None:
        notices.append((tenant_id, run_id, event_id))
        if len(notices) == 1:
            message = "simulated outbox unavailable"
            raise OSError(message)

    coordinator = replace(coordinator, on_completed=notify)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    with pytest.raises(OSError, match="outbox unavailable"):
        _ = coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW)
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert not record.canonical_settled
    coordinator.heartbeat(
        profile_sha256=contract_sha256(coordinator.profile), ready=True, reason_code=None, now=NOW
    )
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.canonical_settled
    assert len(notices) == 2
    assert notices[0] == notices[1]
    assert notices[0][:2] == ("tenant-a", "run-one")


def test_output_root_symlink_rejected_before_external_write(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (coordinator.assets.artifact_root / "remote-capture").symlink_to(
        outside, target_is_directory=True
    )
    with pytest.raises(ValueError, match=r"outside_root|root_invalid"):
        _ = coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW)
    assert list(outside.iterdir()) == []


def test_wrong_nonce_never_registers_asset(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    body = upload(lease)
    assert body.provenance is not None
    body = body.model_copy(
        update={"provenance": body.provenance.model_copy(update={"native_export_nonce": "f" * 64})}
    )
    with pytest.raises(ValueError, match="provenance_invalid"):
        _ = coordinator.complete(lease.job.operation_id, upload=body, now=NOW)
    assert coordinator.assets.get(lease.job.source.scope, lease.job.operation_id) is None


def test_approval_expiry_before_start_produces_no_effect(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path, expires_after=20)
    started = coordinator.start(
        lease.job.operation_id,
        lease_id=lease.lease_id,
        job_sha256=lease.job_sha256,
        now=NOW + timedelta(seconds=30),
    )
    assert started["started"] is False
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.result is not None
    assert record.result.disposition == "no_effect"


def test_completion_after_started_approval_expiry_remains_receipted(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path, expires_after=20)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    assert (
        coordinator.complete(
            lease.job.operation_id, upload=upload(lease), now=NOW + timedelta(minutes=10)
        ).state
        is AgentRunState.COMPLETED
    )


def test_pending_pause_before_start_grants_no_device_execution(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    _ = continue_work(
        coordinator.service,
        "tenant-a",
        "run-one",
        event_id="pause",
        actor_id="member",
        note="잠깐 멈춰줘",
        action="pause",
        now=NOW,
    )
    started = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    assert started["started"] is False
    current = coordinator.service.repository.get("tenant-a", "run-one")
    assert current is not None
    assert current.state is AgentRunState.AWAITING_INPUT


def test_changed_source_after_start_settles_failed_without_current_asset(tmp_path: Path) -> None:
    coordinator, lease = approved(tmp_path)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    coordinator.assets.add(
        lease.job.source.model_copy(update={"revision": 2}), actor_scope=lease.job.source.scope
    )
    completed = coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW)
    assert completed.state is AgentRunState.COMPLETED
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.result is not None
    assert record.result.disposition == "failed"
    assert coordinator.assets.get(lease.job.source.scope, lease.job.operation_id) is None


def test_prestart_no_effect_crash_repairs_canonical_run_on_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, lease = approved(tmp_path, expires_after=20)
    original = MarketingAgentService.complete_deferred

    def crash(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        message = "crash after durable queue completion"
        raise RuntimeError(message)

    monkeypatch.setattr(MarketingAgentService, "complete_deferred", crash)
    with pytest.raises(RuntimeError, match="durable queue"):
        _ = coordinator.start(
            lease.job.operation_id,
            lease_id=lease.lease_id,
            job_sha256=lease.job_sha256,
            now=NOW + timedelta(seconds=30),
        )
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.state == "completed"
    monkeypatch.setattr(MarketingAgentService, "complete_deferred", original)
    assert coordinator.claim(now=NOW + timedelta(seconds=31)) is None
    run = coordinator.service.repository.get("tenant-a", "run-one")
    assert run is not None
    assert run.state is AgentRunState.COMPLETED


@pytest.mark.parametrize("mutation", ["bytes", "deleted"])
def test_missing_or_corrupted_source_after_start_settles_failed(
    tmp_path: Path, mutation: str
) -> None:
    coordinator, lease = approved(tmp_path)
    _ = coordinator.start(
        lease.job.operation_id, lease_id=lease.lease_id, job_sha256=lease.job_sha256, now=NOW
    )
    source = coordinator.assets.artifact_root / lease.job.source.relative_path
    if mutation == "bytes":
        _ = source.write_bytes(b"changed-source-bytes")
    else:
        source.unlink()
    completed = coordinator.complete(lease.job.operation_id, upload=upload(lease), now=NOW)
    assert completed.state is AgentRunState.COMPLETED
    record = coordinator.store.get("tenant-a", lease.job.operation_id)
    assert record is not None
    assert record.result is not None
    assert record.result.disposition == "failed"
    assert coordinator.assets.get(lease.job.source.scope, lease.job.operation_id) is None
