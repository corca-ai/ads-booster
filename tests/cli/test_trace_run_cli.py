from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image
from typer.testing import CliRunner

from tests.runtime.test_trace_run import CAPTURE_JSON, COMPOSE_JSON
from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.cli.trace_run import app
from trace_capture.contracts import CaptureProvenance, DeviceKind, ErrorCode
from trace_capture.runtime.trace_run import TraceRunResult, TraceRunState

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from trace_capture.capture.worker import CaptureRequest, SceneCaptureAdapter


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


def test_run_command_when_local_component_is_supplied_then_it_composes_final_image(
    tmp_path: Path,
) -> None:
    # Given a valid one-scene run job and real local image layers
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-cli",
        "idempotency_key": "run-cli-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    component = tmp_path / "fixture-components.png"
    background.parent.mkdir(parents=True)
    Image.new("RGB", (320, 640), (10, 20, 30)).save(background)
    ui_image = Image.new("RGBA", (320, 640), (0, 0, 0, 255))
    ui_image.putpixel((160, 20), (255, 255, 255, 255))
    ui_image.save(iphone_ui)
    component_image = Image.new("RGBA", (320, 640), (0, 0, 0, 0))
    component_image.putpixel((160, 320), (0, 255, 0, 255))
    component_image.save(component)

    # When the user invokes the installed CLI surface with a local artifact port
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--component-artifact",
            str(component),
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    # Then it reports a completed machine-readable result and writes the final image
    assert result.exit_code == 0
    payload = TraceRunResult.model_validate_json(result.stdout)
    assert payload.state is TraceRunState.COMPLETED
    serialized = payload.model_dump(mode="json")
    assert serialized["capture_provenance"]["source"] == "offline_fixture"
    assert serialized["capture_provenance"]["native_export_binding_verified"] is False
    assert (tmp_path / "outputs" / "final.png").is_file()
    journal = (tmp_path / "state" / "run-cli" / "transitions.jsonl").read_text(
        encoding="utf-8",
    )
    assert '"source":"offline_fixture"' in journal


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


def test_run_command_when_component_artifact_is_missing_then_it_returns_runtime_failure(
    tmp_path: Path,
) -> None:
    # Given a valid run request without a supplied local capture artifact
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-failed",
        "idempotency_key": "run-failed-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")

    # When the user invokes the real CLI without the capture capability input
    result = CliRunner().invoke(
        app,
        ["--job", str(job_path), "--state-root", str(tmp_path / "state")],
    )

    # Then it exits with runtime code 1 and a failed machine-readable result
    assert result.exit_code == 1
    response = TraceRunResult.model_validate_json(result.stdout)
    assert response.state is TraceRunState.FAILED


def test_run_command_when_fixture_is_omitted_then_it_uses_capture_worker_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an injected hardened adapter and a run with real compositing layers
    monkeypatch.setattr("trace_capture.cli.trace_run.build_capture_adapter", cli_capture_adapter)
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

    # When the CLI runs without a local component fixture
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
    monkeypatch.setattr("trace_capture.cli.trace_run.build_capture_adapter", cli_failing_adapter)
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
