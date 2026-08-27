from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PIL import Image
from typer.testing import CliRunner

from ads_booster.cli.trace_run import TraceRunCliErrorPayload, app
from ads_booster.runtime.trace_run import TraceRunRequest, TraceRunResult, TraceRunState
from ads_booster.runtime.trace_run_store import JsonlTraceRunStore
from tests.cli.test_trace_run_cli import cli_capture_adapter
from tests.runtime.test_trace_run import CAPTURE_JSON, COMPOSE_JSON

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_run_command_when_state_journal_is_corrupt_then_it_returns_json_error(
    tmp_path: Path,
) -> None:
    # Given a valid request whose durable journal was corrupted
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-corrupt",
        "idempotency_key": "run-corrupt-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    request = TraceRunRequest.model_validate(payload)
    state_root = tmp_path / "state"
    _ = JsonlTraceRunStore(root=state_root).begin(request)
    journal = state_root / "run-corrupt" / "transitions.jsonl"
    _ = journal.write_text("not-json\n", encoding="utf-8")
    # When the user retries through the real CLI surface
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--state-root",
            str(state_root),
        ],
    )

    # Then it returns a parseable state error instead of a traceback
    assert result.exit_code == 2
    error_payload = TraceRunCliErrorPayload.model_validate_json(result.stdout)
    assert error_payload.status == "invalid_state"
    assert error_payload.error_code.value == "journal_invalid"


def test_run_command_when_capture_output_root_is_a_file_then_it_returns_json_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a valid job and a capture output path occupied by a regular file
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-output-file",
        "idempotency_key": "run-output-file-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    monkeypatch.setattr("ads_booster.cli.trace_run.build_capture_adapter", cli_capture_adapter)
    background.parent.mkdir(parents=True)
    Image.new("RGB", (320, 640), (10, 20, 30)).save(background)
    ui_image = Image.new("RGBA", (320, 640), (0, 0, 0, 255))
    ui_image.putpixel((160, 20), (255, 255, 255, 255))
    ui_image.save(iphone_ui)
    output_root = tmp_path / "outputs"
    _ = output_root.write_bytes(b"not a directory")

    # When the user invokes the real CLI
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--capture-output-root",
            str(tmp_path / "capture"),
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    # Then the runtime failure remains parseable instead of leaking an OSError traceback
    assert result.exit_code == 1
    response = TraceRunResult.model_validate_json(result.stdout)
    assert response.state is TraceRunState.FAILED
    assert response.failure is not None


def test_run_command_when_state_root_is_a_symlink_then_it_returns_json_state_error(
    tmp_path: Path,
) -> None:
    # Given a valid job and a symlink selected as the durable state root
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-state-symlink",
        "idempotency_key": "run-state-symlink-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "state-target"
    target.mkdir()
    state_root = tmp_path / "state"
    state_root.symlink_to(target, target_is_directory=True)
    # When the user invokes the real CLI
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--state-root",
            str(state_root),
        ],
    )

    # Then the symlinked store is rejected through the JSON error boundary
    assert result.exit_code == 2
    error_payload = TraceRunCliErrorPayload.model_validate_json(result.stdout)
    assert error_payload.status == "invalid_state"
    assert error_payload.error_code.value == "store_failed"
    assert not (target / "run-state-symlink" / "transitions.jsonl").exists()


def test_run_command_when_completed_output_is_mutated_then_it_returns_json_artifact_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one successful CLI run with request-bound native capture
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-artifact-mutation",
        "idempotency_key": "run-artifact-mutation-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    monkeypatch.setattr("ads_booster.cli.trace_run.build_capture_adapter", cli_capture_adapter)
    background.parent.mkdir(parents=True)
    Image.new("RGB", (320, 640), (10, 20, 30)).save(background)
    ui_image = Image.new("RGBA", (320, 640), (0, 0, 0, 255))
    ui_image.putpixel((160, 20), (255, 255, 255, 255))
    ui_image.save(iphone_ui)
    state_root = tmp_path / "state"
    first = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--capture-output-root",
            str(tmp_path / "capture"),
            "--state-root",
            str(state_root),
        ],
    )
    assert first.exit_code == 0
    output = tmp_path / "outputs" / "final.png"
    _ = output.write_bytes(b"mutated output")

    # When the same completed run is replayed through the CLI
    second = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--capture-output-root",
            str(tmp_path / "capture"),
            "--state-root",
            str(state_root),
        ],
    )

    # Then artifact integrity failure remains a machine-readable state error
    assert second.exit_code == 2
    error_payload = TraceRunCliErrorPayload.model_validate_json(second.stdout)
    assert error_payload.status == "invalid_state"
    assert error_payload.error_code.value == "artifact_invalid"


def test_run_command_when_capture_is_cancelled_then_it_does_not_claim_old_artifacts(
    tmp_path: Path,
) -> None:
    # Given a valid run job and older output files at the declared compose paths
    job_path = tmp_path / "run.json"
    payload = {
        "schema_version": "trace.run-job.v1",
        "run_id": "run-cancelled-old-output",
        "idempotency_key": "run-cancelled-old-output-key",
        "capture_job": json.loads(CAPTURE_JSON),
        "composite_job": json.loads(COMPOSE_JSON),
    }
    _ = job_path.write_text(json.dumps(payload), encoding="utf-8")
    background = tmp_path / "inputs" / "background.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    old_component = tmp_path / "work" / "trace-components.png"
    old_output = tmp_path / "outputs" / "final.png"
    background.parent.mkdir(parents=True)
    old_component.parent.mkdir(parents=True)
    old_output.parent.mkdir(parents=True)
    Image.new("RGB", (320, 640), (10, 20, 30)).save(background)
    Image.new("RGB", (320, 640), (0, 0, 0)).save(iphone_ui)
    _ = old_component.write_bytes(b"old-component")
    _ = old_output.write_bytes(b"old-output")
    cancel_file = tmp_path / "cancel"
    _ = cancel_file.touch()

    # When capture is cancelled before it produces a new artifact
    result = CliRunner().invoke(
        app,
        [
            "--job",
            str(job_path),
            "--cancel-file",
            str(cancel_file),
            "--state-root",
            str(tmp_path / "state"),
        ],
    )

    # Then the failed run does not claim unrelated files from the job root
    assert result.exit_code == 1
    response = TraceRunResult.model_validate_json(result.stdout)
    assert response.state is TraceRunState.FAILED
    assert response.component_artifact is None
    assert response.output_image is None
