"""Pure worker-specific capture contracts do not inherit the Service host executable."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.models import CaptureProvenance
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureInput,
    build_creative_capture_contract,
    validate_native_capture_result,
)
from tests.marketing.agent_service.test_creative_capture import png, setup_tool

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract


def contract(
    tmp_path: Path, executable: str = "/Users/worker/capture/bin/python"
) -> CodexAppiumJobContract:
    tool, _, invocation = setup_tool(tmp_path)
    run = tool.repository.get("tenant-a", "run-a")
    assert run is not None
    source = tool.assets.get(tool.scope_for_run(run), "background")
    assert source is not None
    return build_creative_capture_contract(
        run=run,
        request=CreativeCaptureInput.model_validate(invocation.input),
        source=source,
        key="a" * 64,
        background_relative="inputs/background.png",
        python_executable=executable,
        device=tool.device,
        appium_server=tool.appium_server,
        export_nonce="b" * 64,
    )


def test_explicit_mac_execution_identity_and_supplied_source(tmp_path: Path) -> None:
    built = contract(tmp_path)
    assert built.python_executable == "/Users/worker/capture/bin/python"
    assert built.export_nonce == "b" * 64
    assert built.device.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"
    assert built.prepared_background.sha256 == sha256(png()).hexdigest()
    assert built.prepared_background.provenance.schema_version == "trace.supplied-background.v1"
    assert "Preserve: Character" in (built.context.promotion_material.creative_direction or "")
    linux = built.model_dump()
    del linux["request_sha256"]
    linux.update(python_executable="/srv/linux/venv/bin/python", launch_arguments=())
    other = type(built).model_validate(linux)
    assert other.python_executable == "/srv/linux/venv/bin/python"
    # Native visual request digest intentionally excludes host execution configuration.
    assert other.request_sha256 == built.request_sha256
    assert type(built).model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize(
    "changed", ["nonce", "device", "hash", "request", "size", "dimensions", "binding"]
)
def test_native_validator_rejects_unbound_metadata(tmp_path: Path, changed: str) -> None:
    built = contract(tmp_path)
    receipt = CaptureProvenance(
        request_sha256=built.request_sha256,
        artifact_sha256=sha256(png()).hexdigest(),
        bundle_id=built.bundle_id,
        device_udid=built.device.udid,
        session_id="fixture",
        byte_size=len(png()),
        width=32,
        height=64,
        source_modified_at_ns=1,
        native_export_nonce=built.export_nonce,
        native_export_binding_verified=True,
    )
    validate_native_capture_result(
        contract=built,
        provenance=receipt,
        image_format="PNG",
        image_sha256=sha256(png()).hexdigest(),
        byte_size=len(png()),
        width=32,
        height=64,
    )
    updates: dict[str, dict[str, object]] = {
        "nonce": {"native_export_nonce": "c" * 64},
        "device": {"device_udid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"},
        "hash": {"artifact_sha256": "c" * 64},
        "request": {"request_sha256": "c" * 64},
        "size": {"byte_size": len(png()) + 1},
        "dimensions": {"width": 33},
        "binding": {"native_export_binding_verified": False},
    }
    forged = receipt.model_copy(update=updates[changed])
    with pytest.raises(ValueError, match="capture_native_provenance_invalid"):
        validate_native_capture_result(
            contract=built,
            provenance=forged,
            image_format="PNG",
            image_sha256=sha256(png()).hexdigest(),
            byte_size=len(png()),
            width=32,
            height=64,
        )


def test_builder_rejects_source_not_bound_to_requested_revision(tmp_path: Path) -> None:
    tool, _, invocation = setup_tool(tmp_path)
    run = tool.repository.get("tenant-a", "run-a")
    assert run is not None
    source = tool.assets.get(tool.scope_for_run(run), "background")
    assert source is not None
    with pytest.raises(ValueError, match="capture_contract_source_mismatch"):
        _ = build_creative_capture_contract(
            run=run,
            request=CreativeCaptureInput.model_validate(invocation.input),
            source=source.model_copy(update={"revision": 2}),
            key="a" * 64,
            background_relative="inputs/background.png",
            python_executable="/Users/worker/venv/bin/python",
            device=tool.device,
            appium_server=tool.appium_server,
            export_nonce="b" * 64,
        )
