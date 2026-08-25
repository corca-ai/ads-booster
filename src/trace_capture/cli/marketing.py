from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated

import typer

from trace_capture.marketing.bridge import MarketingBridge
from trace_capture.marketing.cloudflare_queue import (
    CloudflareQueueClient,
    CloudflareQueueConfig,
    ControlPlaneCallbackClient,
)
from trace_capture.marketing.executors import ArtifactSimulationExecutor
from trace_capture.marketing.inbox import MarketingInbox
from trace_capture.marketing.simulator import LocalMarketingControlPlane, MarketingAccount
from trace_capture.transport.http import create_http_client

app = typer.Typer(no_args_is_help=True, help="Operate the dynamic marketing account loop.")


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
    home: Annotated[Path | None, typer.Option(help="Bridge state root.")] = None,
    once: Annotated[bool, typer.Option(help="Pull and process at most one local task.")] = False,
    poll_seconds: Annotated[float, typer.Option(min=0.1, max=60.0)] = 2.0,
) -> None:
    """Run the external Cloudflare Queue pull consumer.

    Required secrets are read from the environment and never persisted by the bridge.
    The default executor is explicitly simulation-only; production task handlers are
    registered in the composition root as they are enabled.
    """
    account_id = _required("CLOUDFLARE_ACCOUNT_ID")
    queue_id = _required("TRACE_MARKETING_QUEUE_ID")
    queue_token = _required("TRACE_MARKETING_QUEUE_TOKEN")
    control_plane_url = _required("TRACE_MARKETING_CONTROL_PLANE_URL")
    worker_token = _required("TRACE_MARKETING_WORKER_TOKEN")
    root = _home(home) / "marketing-bridge"
    with create_http_client() as http:
        worker = MarketingBridge(
            queue=CloudflareQueueClient(
                http,
                CloudflareQueueConfig(
                    account_id=account_id,
                    queue_id=queue_id,
                    api_token=queue_token,
                ),
            ),
            callbacks=ControlPlaneCallbackClient(http, control_plane_url, worker_token),
            inbox=MarketingInbox(root),
            executor=ArtifactSimulationExecutor(root / "artifacts"),
        )
        recovered = worker.recover()
        if recovered:
            typer.echo(f"recovered {recovered} interrupted task(s)")
        while True:
            active = worker.tick()
            if once:
                return
            if not active:
                time.sleep(poll_seconds)


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
