"""Remote envelopes bind approved inputs and full worker settings beyond native export hashes."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import ToolApproval, contract_sha256
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureInput,
    build_creative_capture_contract,
)
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureJob,
    RemoteCaptureProfile,
    RemoteCaptureUpload,
)
from tests.marketing.agent_service.test_creative_capture import NOW, setup_tool

if TYPE_CHECKING:
    from pathlib import Path


def job(tmp_path: Path) -> RemoteCaptureJob:
    tool, _, invocation = setup_tool(tmp_path)
    run = tool.repository.get("tenant-a", "run-a")
    assert run is not None
    source = tool.assets.get(tool.scope_for_run(run), "background")
    assert source is not None
    profile = RemoteCaptureProfile(
        worker_id="mac",
        tenant_id=run.tenant_id,
        device=tool.device,
        python_executable="/Users/mac/venv/bin/python",
        appium_server=tool.appium_server,
    )
    digest = contract_sha256(invocation)
    contract = build_creative_capture_contract(
        run=run,
        request=CreativeCaptureInput.model_validate(invocation.input),
        source=source,
        key=digest,
        background_relative="inputs/background.png",
        python_executable=profile.python_executable,
        device=profile.device,
        appium_server=profile.appium_server,
        export_nonce="b" * 64,
    )
    approval = ToolApproval(
        schema_version="trace.tool-approval.v1",
        approval_id="approval",
        invocation_sha256=digest,
        approver_id="member",
        decision="granted",
        decided_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    return RemoteCaptureJob(
        operation_id="capture-" + digest[:48],
        invocation=invocation,
        approval=approval,
        profile=profile,
        source=source,
        contract=contract,
    )


def test_envelope_roundtrip_binds_mac_executable_beyond_native_digest(tmp_path: Path) -> None:
    original = job(tmp_path)
    assert RemoteCaptureJob.model_validate_json(original.model_dump_json()) == original
    profile = original.profile.model_copy(update={"python_executable": "/Users/mac/other/python"})
    changed_contract = original.contract.model_copy(
        update={"python_executable": profile.python_executable}
    )
    changed = RemoteCaptureJob.model_validate_json(
        original.model_copy(
            update={"profile": profile, "contract": changed_contract}
        ).model_dump_json()
    )
    assert changed.contract.request_sha256 == original.contract.request_sha256
    assert contract_sha256(changed) != contract_sha256(original)
    with pytest.raises(ValueError, match="contract_mismatch"):
        _ = RemoteCaptureJob.model_validate_json(
            original.model_copy(update={"profile": profile}).model_dump_json()
        )


@pytest.mark.parametrize("target", ["tenant", "approval", "source", "parent", "nonce"])
def test_unbound_job_rejected(tmp_path: Path, target: str) -> None:
    original = job(tmp_path)
    changes: dict[str, object] = {
        "tenant": original.profile.model_copy(update={"tenant_id": "other"}),
        "approval": original.approval.model_copy(update={"invocation_sha256": "f" * 64}),
        "source": original.source.model_copy(update={"revision": 2}),
        "parent": original.contract.prepared_background.model_copy(update={"sha256": "f" * 64}),
        "nonce": original.contract.model_copy(update={"export_nonce": "f" * 64}),
    }
    if target == "parent":
        forged = original.model_copy(
            update={
                "contract": original.contract.model_copy(
                    update={"prepared_background": changes[target]}
                )
            }
        )
    else:
        field = {
            "tenant": "profile",
            "approval": "approval",
            "source": "source",
            "nonce": "contract",
        }[target]
        forged = original.model_copy(update={field: changes[target]})
    with pytest.raises(ValueError, match=r"mismatch|invalid|must match"):
        _ = RemoteCaptureJob.model_validate_json(forged.model_dump_json())


def test_upload_requires_success_evidence_and_no_evidence_for_failure() -> None:
    with pytest.raises(ValueError, match="success_payload_required"):
        _ = RemoteCaptureUpload(job_sha256="a" * 64, lease_id="lease", disposition="succeeded")
    with pytest.raises(ValueError, match="failure_payload_invalid"):
        _ = RemoteCaptureUpload(
            job_sha256="a" * 64,
            lease_id="lease",
            disposition="failed",
            png_base64="fake",
            error_code="worker_failed",
        )
    assert (
        RemoteCaptureUpload(
            job_sha256="a" * 64,
            lease_id="lease",
            disposition="no_effect",
            error_code="source_stale",
        ).provenance
        is None
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python_executable", "relative/python"),
        ("appium_server", "https://external.example/appium"),
        ("timeout_seconds", 29),
    ],
)
def test_profile_rejects_unsafe_worker_configuration(
    tmp_path: Path, field: str, value: object
) -> None:
    profile = job(tmp_path).profile
    with pytest.raises(ValueError, match=r"invalid|Appium|greater|local|http|loopback"):
        _ = RemoteCaptureProfile.model_validate_json(
            profile.model_copy(update={field: value}).model_dump_json()
        )
