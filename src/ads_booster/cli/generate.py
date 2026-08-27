from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import replace
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, assert_never

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from ads_booster.candidate_generation.background_factory import JudgedBackgroundFetcherFactory
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.config.settings import AgentSettings
from ads_booster.connectors.trace.v1.composition import TraceV1Composition
from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.runtime.generate_one import (
    GenerateOneError,
    GenerateOneOptions,
)
from ads_booster.runtime.trace_run import TraceRunState
from ads_booster.tools.paths import resolve_workspace_path
from ads_booster.transport.http import create_http_client

app = typer.Typer(add_completion=False, no_args_is_help=False)
DEFAULT_OUTPUT_ROOT = Path(".trace-agent/generated")
GenerateOneStatus = Literal["invalid_context", "invalid_config", "generation_failed"]
CONTEXT_INVALID: Final = "context_invalid"
GENERATION_FAILED: Final = "generation_failed"
PATH_DENIED: Final = "path_denied"


class GenerateOneErrorPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: GenerateOneStatus
    error_code: str
    message: str


@app.callback(invoke_without_command=True)
def generate_one(
    context_file: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    output_root: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_OUTPUT_ROOT,
    appium_server: Annotated[str, typer.Option()] = "http://127.0.0.1:4723",
    timeout_seconds: Annotated[float, typer.Option(min=1, max=3600)] = 120,
) -> None:
    workspace = Path.cwd().resolve()
    try:
        bundle = _load_context(_required_path(workspace, context_file, "context"))
        options = GenerateOneOptions(
            output_root=_required_path(workspace, output_root, "output"),
            appium_server=appium_server,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, UnicodeError, ValidationError, GenerateOneError) as error:
        _emit_error("invalid_context", CONTEXT_INVALID, str(error), exit_code=2)
        return

    try:
        readiness = DefaultCaptureReadiness(appium_server=options.appium_server)
        options = replace(options, capture_readiness=readiness)
        with create_http_client() as http:
            settings = AgentSettings.from_environment(workspace)
            result = (
                TraceV1Composition(
                    home=workspace / ".trace-agent",
                    settings=settings,
                    http=http,
                    options=options,
                    background_fetchers=JudgedBackgroundFetcherFactory(
                        http=http,
                        settings=settings,
                    ),
                    reference_root=workspace,
                )
                .build()
                .run(bundle)
            )
    except (GenerateOneError, OSError) as error:
        _emit_error(GENERATION_FAILED, GENERATION_FAILED, str(error), exit_code=1)
        return

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


def _load_context(path: Path) -> MarketingContextBundle:
    try:
        return MarketingContextBundle.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise GenerateOneError(
            CONTEXT_INVALID,
            f"marketing context could not be loaded: {path}",
        ) from error


def _required_path(workspace: Path, path: Path, label: str) -> Path:
    resolved = resolve_workspace_path(workspace, path.as_posix())
    if resolved is None:
        raise GenerateOneError(PATH_DENIED, f"{label} path must stay inside the workspace")
    return resolved


def _emit_error(
    status: GenerateOneStatus,
    error_code: str,
    message: str,
    exit_code: int,
) -> None:
    typer.echo(
        GenerateOneErrorPayload(
            status=status,
            error_code=error_code,
            message=message,
        ).model_dump_json()
    )
    raise typer.Exit(code=exit_code)
