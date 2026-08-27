# pyright: reportUnnecessaryComparison=false

from pathlib import Path
from typing import Annotated, assert_never

import typer
from pydantic import ValidationError

from ads_booster.cli.capture import CliErrorPayload, JobLoadError, JobLoadErrorCode
from ads_booster.composition.composite_worker import CompositeWorker
from ads_booster.contracts import ErrorCode, JobStatus, MarketingCompositeJob

app = typer.Typer(no_args_is_help=True)


def load_composite_job(job_path: Path) -> MarketingCompositeJob:
    try:
        raw_job = job_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise JobLoadError(
            code=JobLoadErrorCode.READ_FAILED,
            message=f"composite job could not be read: {job_path}",
        ) from error
    try:
        return MarketingCompositeJob.model_validate_json(raw_job)
    except ValidationError as error:
        raise JobLoadError(
            code=JobLoadErrorCode.VALIDATION_FAILED,
            message=f"composite job failed validation with {error.error_count()} error(s)",
        ) from error


@app.command()
def compose(
    job: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
) -> None:
    try:
        composite_job = load_composite_job(job)
    except JobLoadError as error:
        typer.echo(
            CliErrorPayload(
                error_code=error.code,
                message=error.message,
            ).model_dump_json(),
        )
        raise typer.Exit(code=2) from error

    try:
        result = CompositeWorker().run(job=composite_job, job_root=job.parent)
    except OSError as error:
        typer.echo(
            CliErrorPayload(
                status="compose_failed",
                error_code=ErrorCode.COMPOSITION_FAILED,
                message=f"composition filesystem operation failed: {error}",
            ).model_dump_json(),
        )
        raise typer.Exit(code=1) from error
    typer.echo(result.model_dump_json())
    match result.status:
        case JobStatus.COMPLETED:
            return
        case JobStatus.PARTIAL | JobStatus.FAILED:
            raise typer.Exit(code=1)
        case _ as unreachable:
            assert_never(unreachable)
