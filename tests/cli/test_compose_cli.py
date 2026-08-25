from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PIL import Image
from typer.testing import CliRunner

from tests.contracts.test_composite_contracts import VALID_COMPOSITE_JOB
from trace_capture.cli.capture import CliErrorPayload
from trace_capture.cli.compose import app
from trace_capture.contracts import (
    ErrorCode,
    JobStatus,
    MarketingCompositeJob,
    MarketingCompositeResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class CompositeWorkerTarget(Protocol):
    def run(self, job: MarketingCompositeJob, job_root: Path) -> MarketingCompositeResult: ...


def test_compose_command_when_job_is_complete(tmp_path: Path) -> None:
    # Given a valid v2 job and all three layer files
    job_path = tmp_path / "job.json"
    _ = job_path.write_text(VALID_COMPOSITE_JOB, encoding="utf-8")
    background = tmp_path / "inputs" / "background.jpg"
    components = tmp_path / "work" / "trace-components.png"
    iphone_ui = tmp_path / "inputs" / "iphone-ui.png"
    background.parent.mkdir(parents=True)
    components.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), (10, 20, 30)).save(background)
    component_image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    component_image.putpixel((0, 0), (0, 255, 0, 255))
    component_image.save(components)
    ui_image = Image.new("RGB", (2, 2), (0, 0, 0))
    ui_image.putpixel((1, 1), (255, 255, 255))
    ui_image.save(iphone_ui)

    # When the user invokes the real composition CLI
    result = CliRunner().invoke(app, ["--job", str(job_path)])

    # Then it returns a completed machine-readable result and final PNG
    assert result.exit_code == 0
    payload = MarketingCompositeResult.model_validate_json(result.stdout)
    assert payload.status is JobStatus.COMPLETED
    assert (tmp_path / "outputs" / "final.png").is_file()


def test_compose_command_when_result_write_fails_returns_json_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a valid composition job and a worker that reports a filesystem failure
    job_path = tmp_path / "job.json"
    _ = job_path.write_text(VALID_COMPOSITE_JOB, encoding="utf-8")

    def raise_filesystem_error(
        _worker: CompositeWorkerTarget,
        job: MarketingCompositeJob,
        job_root: Path,
    ) -> MarketingCompositeResult:
        del job, job_root
        message = "read-only output"
        raise OSError(message)

    monkeypatch.setattr("trace_capture.cli.compose.CompositeWorker.run", raise_filesystem_error)

    # When the user invokes the composition CLI
    result = CliRunner().invoke(app, ["--job", str(job_path)])

    # Then the boundary emits a typed composition failure without a traceback
    assert result.exit_code == 1
    payload = CliErrorPayload.model_validate_json(result.stdout)
    assert payload.status == "compose_failed"
    assert payload.error_code is ErrorCode.COMPOSITION_FAILED
