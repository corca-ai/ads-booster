from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, assert_never, override

import typer
from pydantic import ValidationError

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.factory import build_capture_adapter
from ads_booster.capture.worker import CaptureExecutionOptions
from ads_booster.contracts import ContractModel, ErrorCode
from ads_booster.runtime.trace_run import (
    LocalComposePort,
    TraceRunRequest,
    TraceRunRunner,
    TraceRunState,
)
from ads_booster.runtime.trace_run_capture import CaptureWorkerPort
from ads_booster.runtime.trace_run_store import (
    ArtifactIntegrityError,
    ConcurrentTransitionError,
    IdempotencyConflictError,
    InvalidRunJournalError,
    InvalidTransitionError,
    JsonlTraceRunStore,
    TraceRunStoreIOError,
)

app = typer.Typer(no_args_is_help=True)
DEFAULT_STATE_ROOT: Final = Path(".trace-runs")
DEFAULT_CAPTURE_OUTPUT_ROOT: Final = Path("appium/outputs")


class TraceRunCliErrorCode(StrEnum):
    READ_FAILED = "job_read_failed"
    VALIDATION_FAILED = "job_validation_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    JOURNAL_INVALID = "journal_invalid"
    STORE_FAILED = "store_failed"
    ARTIFACT_INVALID = "artifact_invalid"
    TRANSITION_CONFLICT = "transition_conflict"
    TRANSITION_INVALID = "transition_invalid"


class TraceRunCliErrorPayload(ContractModel):
    status: str = "invalid_job"
    error_code: TraceRunCliErrorCode | ErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class TraceRunCliError(Exception):
    code: TraceRunCliErrorCode
    message: str

    @override
    def __str__(self) -> str:
        return self.message


def load_trace_run_request(job_path: Path) -> TraceRunRequest:
    try:
        payload = job_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise TraceRunCliError(
            code=TraceRunCliErrorCode.READ_FAILED,
            message=f"trace run job could not be read: {job_path}",
        ) from error
    try:
        return TraceRunRequest.model_validate_json(payload)
    except ValidationError as error:
        raise TraceRunCliError(
            code=TraceRunCliErrorCode.VALIDATION_FAILED,
            message=f"trace run job failed validation with {error.error_count()} error(s)",
        ) from error


@app.command()
def run(
    job: Annotated[Path, typer.Option(dir_okay=False)],
    state_root: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_STATE_ROOT,
    capture_output_root: Annotated[
        Path, typer.Option(file_okay=False)
    ] = DEFAULT_CAPTURE_OUTPUT_ROOT,
    appium_server: Annotated[str, typer.Option()] = "http://127.0.0.1:4723",
    timeout_seconds: Annotated[float, typer.Option(min=1, max=3600)] = 120,
    cancel_file: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    try:
        request = load_trace_run_request(job)
    except TraceRunCliError as error:
        typer.echo(_error_payload(error))
        raise typer.Exit(code=2) from error
    try:
        capture_port = CaptureWorkerPort(
            adapter=build_capture_adapter(
                device_kind=request.capture_job.device.kind,
                appium_server=appium_server,
            ),
            options=CaptureExecutionOptions(
                timeout_seconds=timeout_seconds,
                cancel_file=cancel_file,
            ),
            output_root=capture_output_root,
        )
    except CaptureAdapterError as error:
        typer.echo(
            TraceRunCliErrorPayload(
                status="invalid_config",
                error_code=error.code,
                message=error.message,
            ).model_dump_json()
        )
        raise typer.Exit(code=2) from error
    runner = TraceRunRunner(
        store=JsonlTraceRunStore(root=state_root),
        capture_port=capture_port,
        compose_port=LocalComposePort(),
    )
    try:
        result = runner.run(request=request, job_root=job.parent)
    except IdempotencyConflictError as error:
        cli_error = TraceRunCliError(
            code=TraceRunCliErrorCode.IDEMPOTENCY_CONFLICT,
            message=str(error),
        )
        typer.echo(_error_payload(cli_error, status="invalid_state"))
        raise typer.Exit(code=2) from error
    except InvalidRunJournalError as error:
        cli_error = TraceRunCliError(
            code=TraceRunCliErrorCode.JOURNAL_INVALID,
            message=str(error),
        )
        typer.echo(_error_payload(cli_error, status="invalid_state"))
        raise typer.Exit(code=2) from error
    except (
        ArtifactIntegrityError,
        ConcurrentTransitionError,
        InvalidTransitionError,
        TraceRunStoreIOError,
    ) as error:
        cli_error = TraceRunCliError(
            code=_store_error_code(error),
            message=str(error),
        )
        typer.echo(_error_payload(cli_error, status="invalid_state"))
        raise typer.Exit(code=2) from error
    except OSError as error:
        cli_error = TraceRunCliError(
            code=TraceRunCliErrorCode.STORE_FAILED,
            message=f"trace run filesystem operation failed: {error}",
        )
        typer.echo(_error_payload(cli_error, status="invalid_state"))
        raise typer.Exit(code=2) from error
    typer.echo(result.model_dump_json())
    match result.state:
        case TraceRunState.COMPLETED:
            return
        case (
            TraceRunState.QUEUED
            | TraceRunState.RUNNING
            | TraceRunState.AWAITING_TOOL
            | TraceRunState.FAILED
            | TraceRunState.ABORTED
            | TraceRunState.UNKNOWN_SIDE_EFFECT
        ):
            raise typer.Exit(code=1)
        case _ as unreachable:
            assert_never(unreachable)


def _error_payload(error: TraceRunCliError, status: str = "invalid_job") -> str:
    return TraceRunCliErrorPayload(
        status=status,
        error_code=error.code,
        message=error.message,
    ).model_dump_json()


def _store_error_code(
    error: ArtifactIntegrityError
    | ConcurrentTransitionError
    | InvalidTransitionError
    | TraceRunStoreIOError,
) -> TraceRunCliErrorCode:
    match error:
        case ArtifactIntegrityError():
            return TraceRunCliErrorCode.ARTIFACT_INVALID
        case ConcurrentTransitionError():
            return TraceRunCliErrorCode.TRANSITION_CONFLICT
        case InvalidTransitionError():
            return TraceRunCliErrorCode.TRANSITION_INVALID
        case TraceRunStoreIOError():
            return TraceRunCliErrorCode.STORE_FAILED
        case _ as unreachable:
            assert_never(unreachable)
