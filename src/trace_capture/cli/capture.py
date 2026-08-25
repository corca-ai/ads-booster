# pyright: reportUnnecessaryComparison=false

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, assert_never, override

import typer
from pydantic import ValidationError

from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.capture.factory import build_capture_adapter
from trace_capture.capture.worker import (
    CaptureExecutionOptions,
    CaptureWorker,
)
from trace_capture.contracts import CaptureJob, ContractModel, ErrorCode, JobStatus

app = typer.Typer(no_args_is_help=True)
DEFAULT_OUTPUT_ROOT: Final = Path("appium/outputs")

__all__ = ["CliErrorPayload", "JobLoadErrorCode", "app", "build_capture_adapter"]


class JobLoadErrorCode(StrEnum):
    READ_FAILED = "job_read_failed"
    VALIDATION_FAILED = "job_validation_failed"


class CliErrorPayload(ContractModel):
    status: Literal[
        "invalid_job",
        "invalid_config",
        "capture_failed",
        "compose_failed",
    ] = "invalid_job"
    error_code: JobLoadErrorCode | ErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class JobLoadError(Exception):
    code: JobLoadErrorCode
    message: str

    @override
    def __str__(self) -> str:
        return self.message


def load_capture_job(job_path: Path) -> CaptureJob:
    try:
        raw_job = job_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise JobLoadError(
            code=JobLoadErrorCode.READ_FAILED,
            message=f"capture job could not be read: {job_path}",
        ) from error
    try:
        return CaptureJob.model_validate_json(raw_job)
    except ValidationError as error:
        raise JobLoadError(
            code=JobLoadErrorCode.VALIDATION_FAILED,
            message=f"capture job failed validation with {error.error_count()} error(s)",
        ) from error


@app.command()
def capture(
    job: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    output_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT_ROOT,
    appium_server: Annotated[str, typer.Option()] = "http://127.0.0.1:4723",
    timeout_seconds: Annotated[float, typer.Option(min=1, max=3600)] = 120,
    cancel_file: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    try:
        capture_job = load_capture_job(job)
    except JobLoadError as error:
        typer.echo(
            CliErrorPayload(
                error_code=error.code,
                message=error.message,
            ).model_dump_json(),
        )
        raise typer.Exit(code=2) from error

    try:
        adapter = build_capture_adapter(
            device_kind=capture_job.device.kind,
            appium_server=appium_server,
        )
    except CaptureAdapterError as error:
        typer.echo(
            CliErrorPayload(
                status="invalid_config",
                error_code=error.code,
                message=error.message,
            ).model_dump_json(),
        )
        raise typer.Exit(code=2) from error
    try:
        result = CaptureWorker(
            adapter=adapter,
            options=CaptureExecutionOptions(
                timeout_seconds=timeout_seconds,
                cancel_file=cancel_file,
            ),
        ).run(
            job=capture_job,
            input_root=job.parent,
            output_root=output_root,
        )
    except CaptureAdapterError as error:
        typer.echo(
            CliErrorPayload(
                status="capture_failed",
                error_code=error.code,
                message=error.message,
            ).model_dump_json(),
        )
        raise typer.Exit(code=1) from error
    except OSError as error:
        typer.echo(
            CliErrorPayload(
                status="capture_failed",
                error_code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"capture filesystem operation failed: {error}",
            ).model_dump_json(),
        )
        raise typer.Exit(code=1) from error
    typer.echo(result.model_dump_json())
    status = result.status
    match status:
        case JobStatus.COMPLETED:
            return
        case JobStatus.PARTIAL | JobStatus.FAILED:
            raise typer.Exit(code=1)
        case _ as unreachable:
            assert_never(unreachable)
