from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image
from typer.testing import CliRunner

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.cli.trace_run import app
from ads_booster.contracts import CaptureProvenance, DeviceKind, ErrorCode
from ads_booster.runtime.trace_run import TraceRunResult, TraceRunState
from tests.runtime.test_trace_run import CAPTURE_JSON, COMPOSE_JSON

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ads_booster.capture.worker import CaptureRequest, SceneCaptureAdapter


@dataclass(frozen=True, slots=True)
class CliCaptureAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        image = Image.new("RGBA", (320, 640), (0, 0, 0, 0))
        image.putpixel((160, 320), (0, 255, 0, 255))
        image.save(request.destination, format="PNG")
        content = request.destination.read_bytes()
        return CaptureProvenance(
            request_sha256="a" * 64,
            artifact_sha256=sha256(content).hexdigest(),
            bundle_id="com.corca.Trace",
            device_udid=request.device.udid,
            session_id="session-01",
            byte_size=len(content),
            width=320,
            height=640,
            source_modified_at_ns=1,
            source="native_appium",
            native_export_nonce="b" * 64,
            native_export_binding_verified=True,
        )


@dataclass(frozen=True, slots=True)
class CliFailingAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(code=ErrorCode.EXPORT_INVALID, message="invalid export")


def cli_capture_adapter(device_kind: DeviceKind, appium_server: str) -> SceneCaptureAdapter:
    del device_kind, appium_server
    return CliCaptureAdapter()


def cli_failing_adapter(device_kind: DeviceKind, appium_server: str) -> SceneCaptureAdapter:
    del device_kind, appium_server
    return CliFailingAdapter()


def test_run_command_when_job_is_invalid_then_it_returns_machine_readable_usage_error(
    tmp_path: Path,
) -> None:
    # Given malformed run input at the CLI boundary
    job_path = tmp_path / "invalid.json"
    _ = job_path.write_text("{}", encoding="utf-8")

    # When the user invokes the real CLI
    result = CliRunner().invoke(
        app, ["--job", str(job_path), "--state-root", str(tmp_path / "state")]
    )

    # Then it exits with usage code 2 and a parseable error payload
    assert result.exit_code == 2
    assert '"status":"invalid_job"' in result.stdout


def test_run_command_uses_native_capture_worker_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an injected hardened adapter and a run with real compositing layers
    monkeypatch.setattr("ads_booster.cli.trace_run.build_capture_adapter", cli_capture_adapter)
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-appium",
        "idempotency_key": "run-appium-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    background.parent.mkdir(parents=True)
    Image.new("RGB", (320, 640), (10, 20, 30)).save(background)
    ui_image = Image.new("RGBA", (320, 640), (0, 0, 0, 255))
    ui_image.putpixel((160, 20), (255, 255, 255, 255))
    ui_image.save(iphone_ui)

    # When the CLI runs through its only native capture path
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--capture-output-root",
            str(tmp_path / "capture-output"),
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    # Then the final marketing image uses the component file produced by CaptureWorker
    assert result.exit_code == 0
    assert (tmp_path / "capture-output" / "run-appium" / "capture-01" / "scene-01.png").is_file()
    assert (tmp_path / "outputs" / "final.png").is_file()


def test_run_command_when_appium_endpoint_is_remote_then_it_returns_config_error(
    tmp_path: Path,
) -> None:
    # Given a valid TraceRun job with a non-loopback Appium endpoint
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-remote",
        "idempotency_key": "run-remote-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")

    # When the CLI selects the real capture path
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--appium-server",
            "http://example.com:4723",
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    # Then it fails with a machine-readable configuration error before Appium starts
    assert result.exit_code == 2
    assert '"status":"invalid_config"' in result.stdout


def test_run_command_when_capture_fails_then_it_returns_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the hardened adapter reports a typed component export failure
    monkeypatch.setattr("ads_booster.cli.trace_run.build_capture_adapter", cli_failing_adapter)
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-capture-failed",
        "idempotency_key": "run-capture-failed-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")

    # When the CLI runs without a local fixture override
    result = CliRunner().invoke(
        app,
        ["--job", str(job_path), "--state-root", str(tmp_path / "state")],
    )

    # Then capture failure propagates as exit code 1 with a failed run result
    assert result.exit_code == 1
    response = TraceRunResult.model_validate_json(result.stdout)
    assert response.state is TraceRunState.FAILED
