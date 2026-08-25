from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from trace_capture.candidate_generation import (
    build_candidate_generator,
    build_candidate_image_runner,
)
from trace_capture.config.settings import AgentSettings
from trace_capture.marketing.bridge import MarketingBridge
from trace_capture.marketing.cloudflare_queue import (
    CloudflareQueueClient,
    CloudflareQueueConfig,
    ControlPlaneCallbackClient,
)
from trace_capture.marketing.executors import (
    ArtifactSimulationExecutor,
    CandidatePipelineExecutor,
)
from trace_capture.marketing.inbox import MarketingInbox
from trace_capture.marketing.native_capture import (
    HostedCaptureRoutingExecutor,
    HostedWorkspaceCaptureExecutor,
)
from trace_capture.marketing.service import (
    CredentialProvider,
    MarketingBridgeConfigStore,
    MarketingBridgeServiceConfig,
    MarketingBridgeServiceError,
    resolve_bridge_credentials,
)
from trace_capture.marketing.simulator import LocalMarketingControlPlane, MarketingAccount
from trace_capture.service.worker import build_production_runner
from trace_capture.transport.http import create_http_client
from trace_capture.workspace import SqliteWorkspaceStore

app = typer.Typer(no_args_is_help=True, help="Operate the dynamic marketing account loop.")


class BridgeExecutor(StrEnum):
    SIMULATION = "simulation"
    CANDIDATE_PIPELINE = "candidate-pipeline"


@dataclass(frozen=True, slots=True)
class BridgeRuntime:
    agent_home: Path
    account_id: str
    queue_id: str
    queue_token: str
    control_plane_url: str
    worker_token: str
    once: bool
    poll_seconds: float
    executor: BridgeExecutor


@app.command("simulate")
def simulate(
    account_id: Annotated[str, typer.Option(help="Stable lower-case marketing account ID.")],
    country: Annotated[str, typer.Option(help="Country or locale code.")] = "KR",
    home: Annotated[
        Path | None,
        typer.Option(help="Simulation state root; defaults under TRACE_AGENT_HOME."),
    ] = None,
    auto_approve: Annotated[
        bool,
        typer.Option(help="Exercise the approval event and complete the simulated loop."),
    ] = False,
) -> None:
    root = _home(home) / "marketing-simulation"
    control = LocalMarketingControlPlane(root)
    _ = control.register_account(MarketingAccount(account_id=account_id, country=country))
    run = control.start_run(account_id, auto_approve=auto_approve)
    typer.echo(run.model_dump_json(indent=2))


@app.command("bridge")
def bridge(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    once: Annotated[bool, typer.Option(help="Pull and process at most one local task.")] = False,
    poll_seconds: Annotated[float, typer.Option(min=0.1, max=60.0)] = 2.0,
    executor: Annotated[
        BridgeExecutor,
        typer.Option(
            help=(
                "simulation, or candidate-pipeline for real provider candidate generation "
                "and native Appium capture; publication stays simulated"
            )
        ),
    ] = BridgeExecutor.SIMULATION,
) -> None:
    """Run the external Cloudflare Queue pull consumer.

    Required secrets are read from the environment and never persisted by the bridge.
    The default executor is explicitly simulation-only; production task handlers are
    registered in the composition root as they are enabled.
    """
    agent_home = _home(home)
    _run_bridge(
        BridgeRuntime(
            agent_home=agent_home,
            account_id=_required("CLOUDFLARE_ACCOUNT_ID"),
            queue_id=_required("TRACE_MARKETING_QUEUE_ID"),
            queue_token=_required("TRACE_MARKETING_QUEUE_TOKEN"),
            control_plane_url=_required("TRACE_MARKETING_CONTROL_PLANE_URL"),
            worker_token=_required("TRACE_MARKETING_WORKER_TOKEN"),
            once=once,
            poll_seconds=poll_seconds,
            executor=executor,
        )
    )


@app.command("bridge-configure")
def bridge_configure(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    executor: Annotated[BridgeExecutor, typer.Option()] = BridgeExecutor.CANDIDATE_PIPELINE,
    poll_seconds: Annotated[float, typer.Option(min=0.1, max=60.0)] = 2.0,
    credential_provider: Annotated[
        CredentialProvider,
        typer.Option(help="environment, or command for an external secret manager."),
    ] = CredentialProvider.ENVIRONMENT,
    credential_command: Annotated[
        list[str] | None,
        typer.Option(
            "--credential-command",
            help="One argv item; repeat to build the external secret command without a shell.",
        ),
    ] = None,
) -> None:
    """Write portable non-secret enrollment for a worker on any computer."""
    agent_home = _home(home)
    try:
        config = MarketingBridgeServiceConfig(
            account_id=_required("CLOUDFLARE_ACCOUNT_ID"),
            queue_id=_required("TRACE_MARKETING_QUEUE_ID"),
            control_plane_url=_required("TRACE_MARKETING_CONTROL_PLANE_URL"),
            executor=executor.value,
            poll_seconds=poll_seconds,
            credential_provider=credential_provider,
            credential_command=tuple(credential_command or ()),
        )
        MarketingBridgeConfigStore(agent_home).save(config)
    except (MarketingBridgeServiceError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"worker config: {MarketingBridgeConfigStore(agent_home).path}")
    typer.echo(f"credential provider: {config.credential_provider}")


@app.command("bridge-service", hidden=True)
def bridge_service() -> None:
    """Portable supervisor entrypoint with externally injected credentials."""
    agent_home = _home(None)
    try:
        config = MarketingBridgeConfigStore(agent_home).load()
        credentials = resolve_bridge_credentials(config)
    except MarketingBridgeServiceError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    _run_bridge(
        BridgeRuntime(
            agent_home=agent_home,
            account_id=config.account_id,
            queue_id=config.queue_id,
            queue_token=credentials.queue_token,
            control_plane_url=config.control_plane_url,
            worker_token=credentials.worker_token,
            once=False,
            poll_seconds=config.poll_seconds,
            executor=BridgeExecutor(config.executor),
        )
    )


def _run_bridge(runtime: BridgeRuntime) -> None:
    root = runtime.agent_home / "marketing-bridge"
    simulation = ArtifactSimulationExecutor(root / "artifacts")
    with create_http_client() as http:
        active_executor = simulation
        review_store = None
        if runtime.executor is BridgeExecutor.CANDIDATE_PIPELINE:
            settings = AgentSettings.from_environment()
            store = SqliteWorkspaceStore(runtime.agent_home)
            review_store = store
            candidate_pipeline = CandidatePipelineExecutor(
                generator=build_candidate_generator(settings, store),
                image_runner=build_candidate_image_runner(settings, runtime.agent_home, store),
                store=store,
                artifact_root=runtime.agent_home,
                fallback=simulation,
            )
            active_executor = HostedCaptureRoutingExecutor(
                hosted=HostedWorkspaceCaptureExecutor(
                    runner=build_production_runner(runtime.agent_home, http),
                    output_root=runtime.agent_home / "generated",
                ),
                fallback=candidate_pipeline,
            )
        worker = MarketingBridge(
            queue=CloudflareQueueClient(
                http,
                CloudflareQueueConfig(
                    account_id=runtime.account_id,
                    queue_id=runtime.queue_id,
                    api_token=runtime.queue_token,
                ),
            ),
            callbacks=ControlPlaneCallbackClient(
                http,
                runtime.control_plane_url,
                runtime.worker_token,
            ),
            inbox=MarketingInbox(root),
            executor=active_executor,
            review_store=review_store,
        )
        recovered = worker.recover()
        if recovered:
            typer.echo(f"recovered {recovered} interrupted task(s)")
        while True:
            active = worker.tick()
            if runtime.once:
                return
            if not active:
                time.sleep(runtime.poll_seconds)


def _home(configured: Path | None) -> Path:
    if configured is not None:
        return configured.expanduser()
    value = os.environ.get("TRACE_AGENT_HOME")
    return Path(value).expanduser() if value else Path.home() / ".trace-agent"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    raise typer.BadParameter(f"required environment variable is missing: {name}")
