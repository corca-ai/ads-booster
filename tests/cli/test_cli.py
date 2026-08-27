from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ads_booster.cli.capture import CliErrorPayload, JobLoadErrorCode, app
from ads_booster.contracts import CaptureResult, ErrorCode
from tests.capture.test_worker import JOB_JSON

if TYPE_CHECKING:
    from pathlib import Path


def test_capture_command_when_job_is_invalid(tmp_path: Path) -> None:
    # Given a job file with an unsupported schema version
    job_path = tmp_path / "job.json"
    _ = job_path.write_text('{"schema_version":"unknown"}', encoding="utf-8")

    # When the user runs the real CLI boundary
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--output-root",
            str(tmp_path / "outputs"),
        ],
    )

    # Then the CLI fails closed with a machine-readable boundary result
    assert result.exit_code == 2
    payload = CliErrorPayload.model_validate_json(result.stdout)
    assert payload.status == "invalid_job"
    assert payload.error_code is JobLoadErrorCode.VALIDATION_FAILED


def test_capture_command_when_appium_server_is_remote(tmp_path: Path) -> None:
    # Given a valid simulator job and a non-loopback Appium endpoint
    job_path = tmp_path / "job.json"
    _ = job_path.write_text(JOB_JSON, encoding="utf-8")
    background = tmp_path / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")

    # When the user invokes the real CLI with that endpoint
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--appium-server",
            "http://example.com:4723",
        ],
    )

    # Then the CLI returns a typed configuration failure before connecting
    assert result.exit_code == 2
    payload = CliErrorPayload.model_validate_json(result.stdout)
    assert payload.status == "invalid_config"
    assert payload.error_code is ErrorCode.APPIUM_ENDPOINT_REJECTED
    assert "loopback" in payload.message


def test_capture_command_when_physical_device_is_unavailable(tmp_path: Path) -> None:
    # Given a valid physical-iPhone job with its source photo present
    job_path = tmp_path / "job.json"
    _ = job_path.write_text(
        JOB_JSON.replace('"kind": "simulator"', '"kind": "physical"'),
        encoding="utf-8",
    )
    background = tmp_path / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")

    # When the user runs the real CLI without a connected physical device path
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--output-root",
            str(tmp_path / "outputs"),
        ],
    )

    # Then the CLI fails with the physical-device boundary instead of calling simctl
    assert result.exit_code == 1
    payload = CaptureResult.model_validate_json(result.stdout)
    failure = payload.captures[0]
    assert failure.status == "failed"
    assert failure.error.code is ErrorCode.PHYSICAL_DEVICE_UNAVAILABLE


def test_capture_command_when_output_root_cannot_be_created_returns_json_error(
    tmp_path: Path,
) -> None:
    # Given a valid job whose output root is occupied by a regular file
    job_path = tmp_path / "job.json"
    _ = job_path.write_text(JOB_JSON, encoding="utf-8")
    background = tmp_path / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    output_root = tmp_path / "outputs"
    _ = output_root.write_bytes(b"not-a-directory")

    # When the user invokes the capture CLI
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--output-root",
            str(output_root),
        ],
    )

    # Then filesystem failure is a machine-readable non-traceback response
    assert result.exit_code == 1
    payload = CliErrorPayload.model_validate_json(result.stdout)
    assert payload.status == "capture_failed"
    assert payload.error_code is ErrorCode.SCENE_CAPTURE_FAILED
