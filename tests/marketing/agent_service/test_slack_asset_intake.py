"""Actual canonical approval and local asset recovery, using fake Slack HTTP bytes."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import pytest

import ads_booster.channels.slack_asset_intake as module
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope
from ads_booster.creative.creative_asset_links import link_asset

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject

from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    AgentStep,
    AgentStepKind,
    ToolApproval,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.channels.slack_asset_intake import (
    ImportSlackAsset,
    SlackAssetIntakeTool,
    slack_asset_import_descriptor,
    slack_file_inspect_descriptor,
)
from ads_booster.channels.slack_image_files import SlackImageFiles
from ads_booster.channels.slack_image_review import bind_files
from tests.marketing.agent_service.creative_fixtures import NOW, png, setup_assets
from tests.marketing.agent_service.test_slack_image_review import HTTP


def setup(tmp_path: Path) -> tuple[SlackAssetIntakeTool, HTTP, ToolInvocation, ToolDescriptor]:
    seed, _ = setup_assets(tmp_path)
    http = HTTP(png())
    files = SlackImageFiles(
        seed.repository.database_path,
        seed.assets.artifact_root,
        "tenant-a",
        "synthetic-test-token",
        opener=http,
    )
    bind_files(
        seed.repository.database_path,
        tenant_id="tenant-a",
        run_id="run-a",
        channel_id="C1",
        file_ids=("F01",),
    )
    tool = SlackAssetIntakeTool(seed.repository, seed.assets, files, clock=lambda: NOW)
    descriptor = slack_asset_import_descriptor(now=NOW, ready=True)
    request = ImportSlackAsset(
        file_id="F01",
        expected_sha256=contract_sha256({"unused": True}),
        asset_id="imported",
        kind="background_asset",
        source="Team file",
        use_terms="Team permission declaration",
        data_permission="synthetic",
        permission_evidence="Synthetic fixture",
    )
    request = request.model_copy(update={"expected_sha256": hashlib.sha256(png()).hexdigest()})
    payload = request.model_dump(mode="json")
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="import",
        run_id="run-a",
        tenant_id="tenant-a",
        step_id="s",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="import",
        input=payload,
        input_sha256=contract_sha256(payload),
    )
    return tool, http, invocation, descriptor


def approve(
    tool: SlackAssetIntakeTool,
    invocation: ToolInvocation,
    *,
    decision: Literal["granted", "rejected", "revoked"] = "granted",
) -> ToolApproval:
    run = tool.repository.get("tenant-a", "run-a")
    assert run is not None
    approval = ToolApproval(
        schema_version="trace.tool-approval.v1",
        approval_id=f"approval-{run.revision}",
        invocation_sha256=contract_sha256(invocation),
        approver_id="reviewer",
        decision=decision,
        decided_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    payload = approval.model_dump(mode="json")
    record = AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id=approval.approval_id,
        run_id=run.run_id,
        kind=AgentRecordKind.APPROVAL,
        payload_schema_version=approval.schema_version,
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=NOW,
    )
    step = AgentStep(
        schema_version="trace.agent-step.v1",
        step_id=f"step-{run.revision}",
        run_id=run.run_id,
        sequence=run.revision,
        kind=AgentStepKind.APPROVE,
        state="proposed",
        input_sha256=contract_sha256(invocation),
        parent_step_sha256=run.head_step_sha256,
        occurred_at=NOW,
    )
    _ = tool.repository.append_step(
        run, step, state=run.state, expected_revision=run.revision, records=(record,)
    )
    return approval


def test_approval_before_download_and_exact_replay(tmp_path: Path) -> None:
    tool, http, invocation, descriptor = setup(tmp_path)
    with pytest.raises(ValueError, match="approval_required"):
        _ = tool.import_asset(invocation, descriptor)
    assert http.calls == []
    approval = approve(tool, invocation)
    result = tool.import_asset(invocation, descriptor)
    assert result.output["approver_id"] == approval.approver_id
    assert result.output["approval_sha256"] == contract_sha256(approval)
    assert CreativeAsset.model_validate(result.output["asset"]).origin == "human_reported"
    assert result.output["product_support_verified"] is False
    assert tool.import_asset(invocation, descriptor) == result
    _ = approve(tool, invocation, decision="revoked")
    count = len(http.calls)
    with pytest.raises(ValueError, match="not_current"):
        _ = tool.import_asset(invocation, descriptor)
    assert len(http.calls) == count


def test_changed_approved_hash_no_asset(tmp_path: Path) -> None:
    tool, http, invocation, descriptor = setup(tmp_path)
    payload = {**invocation.input, "expected_sha256": "c" * 64}
    invocation = invocation.model_copy(
        update={"input": payload, "input_sha256": contract_sha256(payload)}
    )
    _ = approve(tool, invocation)
    with pytest.raises(ValueError, match="approved_bytes_changed"):
        _ = tool.import_asset(invocation, descriptor)
    assert (
        tool.assets.get(CreativeScope(workspace_id="tenant-a", product_id="trace"), "imported")
        is None
    )
    assert http.calls


def test_other_tenant_and_unbound_run_before_network(tmp_path: Path) -> None:
    tool, http, invocation, _ = setup(tmp_path)
    descriptor = slack_file_inspect_descriptor(now=NOW, ready=True)
    payload: JsonObject = {"file_id": "F01"}
    invocation = invocation.model_copy(
        update={
            "input": payload,
            "input_sha256": contract_sha256(payload),
            "descriptor_sha256": contract_sha256(descriptor),
            "tenant_id": "other",
        }
    )
    with pytest.raises(ValueError, match="tenant_denied"):
        _ = tool.inspect(invocation, descriptor)
    invocation = invocation.model_copy(update={"tenant_id": "tenant-a", "run_id": "missing"})
    with pytest.raises(ValueError, match="run_missing"):
        _ = tool.inspect(invocation, descriptor)
    assert http.calls == []


def test_missing_local_link_repaired_without_duplicate_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    tool, _, invocation, descriptor = setup(tmp_path)
    _ = approve(tool, invocation)
    original = link_asset

    def lost(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        message = "crash before local link commit"
        raise RuntimeError(message)

    monkeypatch.setattr(module, "link_asset", lost)
    with pytest.raises(RuntimeError, match="crash"):
        _ = tool.import_asset(invocation, descriptor)
    monkeypatch.setattr(module, "link_asset", original)
    assert (
        CreativeAsset.model_validate(
            tool.import_asset(invocation, descriptor).output["asset"]
        ).revision
        == 1
    )
    assert (
        CreativeAsset.model_validate(
            tool.import_asset(invocation, descriptor).output["asset"]
        ).revision
        == 1
    )


@pytest.mark.parametrize(
    "field", ["source", "use_terms", "data_permission", "permission_evidence", "expected_sha256"]
)
def test_required_human_metadata(field: str, tmp_path: Path) -> None:
    _, _, invocation, _ = setup(tmp_path)
    payload = dict(invocation.input)
    _ = payload.pop(field)
    with pytest.raises(ValueError, match=r"Field required|Extra inputs"):
        _ = ImportSlackAsset.model_validate(payload)


@pytest.mark.parametrize("field", ["path", "origin", "qa"])
def test_caller_cannot_forge_verified_metadata(field: str, tmp_path: Path) -> None:
    _, _, invocation, _ = setup(tmp_path)
    with pytest.raises(ValueError, match=r"Field required|Extra inputs"):
        _ = ImportSlackAsset.model_validate({**invocation.input, field: "forged"})


def test_changed_invocation_cannot_overwrite_registered_metadata(tmp_path: Path) -> None:
    tool, _, invocation, descriptor = setup(tmp_path)
    _ = approve(tool, invocation)
    first = tool.import_asset(invocation, descriptor)
    payload = {**invocation.input, "source": "Changed declaration"}
    changed = invocation.model_copy(
        update={"input": payload, "input_sha256": contract_sha256(payload)}
    )
    _ = approve(tool, changed)
    with pytest.raises(ValueError, match="import_conflict"):
        _ = tool.import_asset(changed, descriptor)
    stored = tool.assets.get(CreativeScope(workspace_id="tenant-a", product_id="trace"), "imported")
    assert stored == CreativeAsset.model_validate(first.output["asset"])
