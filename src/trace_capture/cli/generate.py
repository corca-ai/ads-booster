from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import replace
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, assert_never

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from trace_capture.auth.browser import BrowserOAuthError
from trace_capture.auth.codex import CodexOAuth, OAuthError
from trace_capture.auth.store import AuthStore, AuthStoreError
from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.capture.factory import build_capture_adapter
from trace_capture.capture.readiness import DefaultCaptureReadiness
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.providers.errors import ProviderError
from trace_capture.providers.image_generation import CodexImageGenerator
from trace_capture.runtime.generate_one import (
    GenerateOneError,
    GenerateOneOptions,
    GenerateOneRunner,
)
from trace_capture.runtime.trace_run import TraceRunState
from trace_capture.tools.paths import resolve_workspace_path
from trace_capture.transport.http import create_http_client

app = typer.Typer(add_completion=False, no_args_is_help=False)
DEFAULT_OUTPUT_ROOT = Path(".trace-agent/generated")
DEFAULT_STATE_ROOT = Path(".trace-agent/state")
DEFAULT_CAPTURE_OUTPUT_ROOT = Path(".trace-agent/capture")
DEFAULT_IPHONE_UI = Path("appium/jobs/composite/inputs/iphone-ui-ai.png")
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
    state_root: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_STATE_ROOT,
    capture_output_root: Annotated[
        Path, typer.Option(file_okay=False)
    ] = DEFAULT_CAPTURE_OUTPUT_ROOT,
    iphone_ui: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)] = (
        DEFAULT_IPHONE_UI
    ),
    appium_server: Annotated[str, typer.Option()] = "http://127.0.0.1:4723",
    timeout_seconds: Annotated[float, typer.Option(min=1, max=3600)] = 120,
    image_model: Annotated[str, typer.Option()] = "gpt-5.6-luna",
) -> None:
    workspace = Path.cwd().resolve()
    try:
        bundle = _load_context(_required_path(workspace, context_file, "context"))
        options = GenerateOneOptions(
            output_root=_required_path(workspace, output_root, "output"),
            state_root=_required_path(workspace, state_root, "state"),
            capture_output_root=_required_path(workspace, capture_output_root, "capture"),
            iphone_ui_path=_required_path(workspace, iphone_ui, "iPhone UI"),
            reference_root=workspace,
            appium_server=appium_server,
            timeout_seconds=timeout_seconds,
            image_model=image_model,
        )
    except (OSError, UnicodeError, ValidationError, GenerateOneError) as error:
        _emit_error("invalid_context", CONTEXT_INVALID, str(error), exit_code=2)
        return

    try:
        readiness = DefaultCaptureReadiness(appium_server=options.appium_server)
        options = replace(options, capture_readiness=readiness)
        adapter = build_capture_adapter(
            device_kind=bundle.device.kind,
            appium_server=options.appium_server,
            readiness=readiness,
        )
        with create_http_client() as http:
            generator = CodexImageGenerator(
                http=http,
                oauth=CodexOAuth(http=http, store=AuthStore.default()),
            )
            result = GenerateOneRunner(
                options=options,
                image_generator=generator,
                capture_adapter=adapter,
            ).run(bundle)
    except (
        BrowserOAuthError,
        OAuthError,
        AuthStoreError,
        CaptureAdapterError,
        ProviderError,
    ) as error:
        _emit_error(GENERATION_FAILED, GENERATION_FAILED, str(error), exit_code=1)
        return
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
