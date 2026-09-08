"""Explicit remote capture worker commands without installation or login mutation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Self

import typer
from pydantic import Field, model_validator

from ads_booster.capture.appium_codex import CodexAppiumJobAdapter
from ads_booster.capture.calendar_preparation import SimctlEventKitCalendarDataPort
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.capture.simulator_photo import SimctlPhotoImporter
from ads_booster.capture.wallpaper_collection import SimctlAppGroupWallpaperCollector
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.capture_readiness import CaptureReadinessProbe
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureProfile,  # noqa: TC001
)
from ads_booster.marketing.canonical_capture_worker import CanonicalCaptureWorker, RemoteCaptureHttp
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable

_MAX_CONFIG_BYTES = 16000


class RemoteWorkerConfig(ContractModel):
    profile: RemoteCaptureProfile
    origin: Annotated[str, Field(min_length=1, max_length=2000)]
    token_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    state_root: Path

    @model_validator(mode="after")
    def pinned_paths(self) -> Self:
        if not self.state_root.is_absolute() or ".." in self.state_root.parts:
            message = "remote_capture_state_root_requires_absolute_path"
            raise ValueError(message)
        _ = RemoteCaptureHttp(self.origin, self.token_env)
        return self


def build_remote_worker(config_path: Path) -> CanonicalCaptureWorker:
    with config_path.open("rb") as stream:
        raw = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(raw) > _MAX_CONFIG_BYTES:
        message = "remote_capture_configuration_too_large"
        raise ValueError(message)
    config = RemoteWorkerConfig.model_validate_json(raw)
    executable = resolve_codex_executable() or Path("/nonexistent/trace-codex")
    codex = CodexCli(executable=executable)
    probe = CaptureReadinessProbe(config.profile.device, config.profile.appium_server, executable)
    worker = CodexAppiumJobAdapter(
        codex=codex,
        simulator=SimctlPhotoImporter(),
        collector=SimctlAppGroupWallpaperCollector(),
        calendar=SimctlEventKitCalendarDataPort(),
        readiness=DefaultCaptureReadiness(appium_server=config.profile.appium_server),
    )
    return CanonicalCaptureWorker(
        root=config.state_root,
        profile=config.profile,
        http=RemoteCaptureHttp(config.origin, config.token_env),
        worker=worker,
        probe=probe.check,
    )


def capture_remote_doctor(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)],
) -> None:
    """Print local readiness JSON without heartbeat, enrollment or device activation."""
    try:
        worker = build_remote_worker(config)
        report = worker.doctor()
    except OSError, ValueError:
        typer.echo(json.dumps({"ready": False, "error": "remote_capture_configuration_invalid"}))
        raise typer.Exit(1) from None
    typer.echo(json.dumps(report, ensure_ascii=False))
    if report.get("ready") is not True:
        raise typer.Exit(1)


def capture_remote_run(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)],
    once: Annotated[
        bool, typer.Option("--once", help="Process one bounded worker iteration.")
    ] = False,
) -> None:
    """Run configured capture work using the named credential environment variable."""
    try:
        worker = build_remote_worker(config)
        while True:
            typer.echo(json.dumps(worker.work_once(), ensure_ascii=False))
            if once:
                return
            time.sleep(2)
    except OSError, ValueError:
        typer.echo(json.dumps({"error": "remote_capture_worker_failed"}))
        raise typer.Exit(1) from None
