"""Fake Mac worker verifies the real capture contract; no device calls occur."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.contracts.models import CaptureProvenance, DeviceKind, DeviceTarget
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_capture import (
    CreativeCaptureInput,
    CreativeCaptureTool,
    creative_capture_descriptor,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.tool_adapters.compatibility import appium_adapter

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (32, 64), "white").save(stream, format="PNG")
    return stream.getvalue()


class FakeWorker:
    def __init__(self) -> None:
        self.calls: int = 0
        self.fail: bool = False
        self.bad_nonce: bool = False

    def ensure_ready(self, contract: CodexAppiumJobContract, control: CaptureControl) -> None:
        control.checkpoint()
        assert contract.context.campaign_id is None

    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance:
        self.calls += 1
        assert background.read_bytes() == png()
        assert (
            contract.prepared_background.provenance.schema_version == "trace.supplied-background.v1"
        )
        assert "Preserve: Character" in (
            contract.context.promotion_material.creative_direction or ""
        )
        assert contract.context.promotion_material.trace_items
        assert job_root.is_dir()
        control.checkpoint()
        if self.fail:
            message = "lost worker response"
            raise RuntimeError(message)
        data = png()
        _ = output.write_bytes(data)
        return CaptureProvenance(
            request_sha256=contract.request_sha256,
            artifact_sha256=sha256(data).hexdigest(),
            bundle_id=contract.bundle_id,
            device_udid=contract.device.udid,
            session_id="fake-session",
            byte_size=len(data),
            width=32,
            height=64,
            source_modified_at_ns=1,
            native_export_nonce="a" * 64 if self.bad_nonce else contract.export_nonce,
            native_export_binding_verified=True,
        )


def setup_tool(tmp_path: Path) -> tuple[CreativeCaptureTool, FakeWorker, ToolInvocation]:
    database = tmp_path / "service.db"
    repository = SqliteAgentRunRepository(database)
    assets = SqliteCreativeAssetRepository(database, tmp_path / "artifacts")
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    source = assets.artifact_root / "background.png"
    _ = source.write_bytes(png())
    asset = CreativeAsset(
        asset_id="background",
        revision=1,
        scope=scope,
        kind="background_asset",
        relative_path="background.png",
        sha256=sha256(png()).hexdigest(),
        source="Team Figma export",
        use_terms="Team-owned synthetic example",
        data_permission="synthetic",
        permission_evidence="Synthetic fixture",
        origin="human_reported",
    )
    assets.add(asset, actor_scope=scope)
    _ = repository.create(
        AgentRun(
            schema_version="trace.agent-run.v1",
            run_id="run-a",
            tenant_id="tenant-a",
            goal=AgentGoal(objective="Capture this background", success_criteria=("Readable",)),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=40),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    worker = FakeWorker()
    tool = CreativeCaptureTool(
        repository=repository,
        assets=assets,
        job_root=assets.artifact_root / "captures",
        worker=worker,
        device=DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.0",
            device_name="iPhone",
        ),
        appium_server="http://127.0.0.1:4723",
        scope_for_run=lambda run: scope,
    )
    request = CreativeCaptureInput.model_validate(
        {
            "background": AssetParent(asset_id=asset.asset_id, revision=1, sha256=asset.sha256),
            "country": "JP",
            "reference_date": NOW,
            "trace_items": [{"title": "Study"}],
            "concept": "Readable calendar",
            "creative_direction": "Use supplied background",
            "data_permission": "synthetic",
            "preserve": ["Character"],
        }
    )
    payload: JsonObject = request.model_dump(mode="json")
    descriptor = creative_capture_descriptor(now=NOW, ready=True)
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="invoke-a",
        run_id="run-a",
        tenant_id="tenant-a",
        step_id="step-a",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="key",
        input=payload,
        input_sha256=contract_sha256(payload),
    )
    return tool, worker, invocation


def test_actual_contract_to_fake_worker_registers_capture_and_replays_without_device(
    tmp_path: Path,
) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    adapter = appium_adapter(executor_id="fake-mac", executor=tool.execute)
    descriptor = creative_capture_descriptor(now=NOW, ready=True)
    result = adapter.execute(invocation, descriptor)
    assert result.output["human_review_required"] is True
    assert result.output["product_support_verified"] is False
    assert "native_trace_capture" in str(result.output)
    assert "ja-JP" in str(result.output)
    assert adapter.execute(invocation, descriptor) == result
    assert worker.calls == 1


def test_uncertain_capture_is_not_repeated_after_restart(tmp_path: Path) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    descriptor = creative_capture_descriptor(now=NOW, ready=True)
    worker.fail = True
    with pytest.raises(RuntimeError, match="lost worker response"):
        _ = tool.execute(invocation, descriptor)
    worker.fail = False
    tool = replace(tool)
    with pytest.raises(ValueError, match="capture_reconciliation_required"):
        _ = tool.execute(invocation, descriptor)
    assert worker.calls == 1


@pytest.mark.parametrize("fault", ["tenant", "source", "nonce"])
def test_authority_source_and_native_export_fail_closed(tmp_path: Path, fault: str) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    if fault == "tenant":
        invocation = invocation.model_copy(update={"tenant_id": "other"})
    elif fault == "source":
        _ = (tool.assets.artifact_root / "background.png").write_bytes(b"changed")
    else:
        worker.bad_nonce = True
    with pytest.raises(
        ValueError,
        match=r"capture_run_not_found|creative_artifact_digest_mismatch|capture_native_provenance_invalid",
    ):
        _ = tool.execute(invocation, creative_capture_descriptor(now=NOW, ready=True))
    assert worker.calls == (1 if fault == "nonce" else 0)


def test_unavailable_descriptor_does_not_start_worker(tmp_path: Path) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    with pytest.raises(ValueError, match="capture_invocation_context_required"):
        _ = tool.execute(invocation, creative_capture_descriptor(now=NOW, ready=False))
    assert worker.calls == 0


def test_cached_result_bytes_are_revalidated(tmp_path: Path) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    descriptor = creative_capture_descriptor(now=NOW, ready=True)
    result = tool.execute(invocation, descriptor)
    asset = CreativeAsset.model_validate(result.output["asset"])
    _ = (tool.assets.artifact_root / asset.relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="creative_artifact_digest_mismatch"):
        _ = tool.execute(invocation, descriptor)
    assert worker.calls == 1


def test_current_parent_revision_required_even_when_old_file_is_unchanged(tmp_path: Path) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    original = tool.assets.get(scope, "background")
    assert original is not None
    revised = original.model_copy(update={"revision": 2})
    tool.assets.add(revised, actor_scope=scope)
    with pytest.raises(ValueError, match="capture_source_not_current"):
        _ = tool.execute(invocation, creative_capture_descriptor(now=NOW, ready=True))
    assert worker.calls == 0


def test_source_parent_symlink_outside_root_is_denied(tmp_path: Path) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    source = tool.assets.get(scope, "background")
    assert source is not None
    folder = tool.assets.artifact_root / "nested"
    folder.mkdir()
    _ = (folder / "background.png").write_bytes(png())
    nested = source.model_copy(update={"revision": 2, "relative_path": "nested/background.png"})
    tool.assets.add(nested, actor_scope=scope)
    external = tmp_path / "external"
    _ = folder.rename(external)
    folder.symlink_to(external, target_is_directory=True)
    payload = dict(invocation.input)
    payload["background"] = {"asset_id": "background", "revision": 2, "sha256": source.sha256}
    invocation = invocation.model_copy(
        update={"input": payload, "input_sha256": contract_sha256(payload)}
    )
    with pytest.raises(ValueError, match="creative_artifact_outside_root"):
        _ = tool.execute(invocation, creative_capture_descriptor(now=NOW, ready=True))
    assert worker.calls == 0


def test_long_canonical_run_keeps_explicit_lineage_without_worker_identifier_failure(
    tmp_path: Path,
) -> None:
    tool, worker, invocation = setup_tool(tmp_path)
    original = tool.repository.get("tenant-a", "run-a")
    assert original is not None
    canonical_id = "slack:" + "a" * 100
    _ = tool.repository.create(original.model_copy(update={"run_id": canonical_id}))
    invocation = invocation.model_copy(update={"run_id": canonical_id})
    result = tool.execute(invocation, creative_capture_descriptor(now=NOW, ready=True))
    assert result.output["canonical_run_id"] == canonical_id
    assert worker.calls == 1
