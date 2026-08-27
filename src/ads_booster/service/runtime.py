# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

import os
import shutil
import signal
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import TYPE_CHECKING, assert_never, override

import uvicorn
from anyio import create_task_group
from pydantic import TypeAdapter

from ads_booster.service.state import (
    BootstrapResult,
    ServiceStateError,
    ServiceStateStore,
    ensure_workspace,
)
from ads_booster.service.worker import (
    ServiceWorkerConfig,
    build_production_runner,
    create_service_worker,
)
from ads_booster.transport.http import create_http_client
from ads_booster.tunnel import CloudflaredTunnel, TunnelStartResult
from ads_booster.web.app import create_app
from ads_booster.workspace import SqliteWorkspaceStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import FrameType

    from fastapi import FastAPI

    from ads_booster.automation import GenerateOnePort
    from ads_booster.service.worker import AutomationServiceWorker


@unique
class TunnelName(StrEnum):
    NONE = "none"
    CLOUDFLARED = "cloudflared"


@dataclass(frozen=True, slots=True)
class ServiceBindError(Exception):
    host: str
    port: int
    detail: str

    @override
    def __str__(self) -> str:
        return f"cannot bind workspace service at {self.host}:{self.port}: {self.detail}"


@dataclass(frozen=True, slots=True)
class PreparedService:
    bootstrap: BootstrapResult
    local_url: str
    port: int
    socket: socket.socket
    tunnel: CloudflaredTunnel | None
    tunnel_result: TunnelStartResult | None


class _WorkspaceServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


def prepare_service(
    home: Path,
    *,
    host: str,
    port: int,
    tunnel_name: TunnelName,
    workspace_name: str | None = None,
) -> PreparedService:
    if host != "127.0.0.1":
        raise ServiceBindError(host, port, "host must be the loopback address 127.0.0.1")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(2048)
    except OSError as error:
        listener.close()
        raise ServiceBindError(host, port, str(error)) from error
    selected_port = TypeAdapter(tuple[str, int]).validate_python(listener.getsockname())[1]
    state_store = ServiceStateStore(home)
    try:
        bootstrap = ensure_workspace(
            SqliteWorkspaceStore(home),
            state_store,
            workspace_name=workspace_name,
        )
        service_state = bootstrap.state.model_copy(
            update={
                "host": host,
                "port": selected_port,
                "tunnel": tunnel_name.value,
                "public_url": None,
            }
        )
        state_store.save(service_state)
    except ServiceStateError:
        listener.close()
        raise
    except OSError as error:
        listener.close()
        raise ServiceStateError(home, str(error)) from error
    local_url = f"http://{host}:{selected_port}"
    active_tunnel: CloudflaredTunnel | None = None
    tunnel_result: TunnelStartResult | None = None
    try:
        match tunnel_name:
            case TunnelName.NONE:
                pass
            case TunnelName.CLOUDFLARED:
                binary = shutil.which("cloudflared")
                active_tunnel = CloudflaredTunnel(
                    binary=_cloudflared_path(binary),
                    log_path=home / "logs" / "cloudflared.log",
                )
                tunnel_result = active_tunnel.start(local_url)
            case unreachable:
                assert_never(unreachable)
        public_url = None if tunnel_result is None else tunnel_result.public_url
        service_state = service_state.model_copy(update={"public_url": public_url})
        state_store.save(service_state)
    except ServiceStateError:
        if (
            active_tunnel is not None
            and tunnel_result is not None
            and tunnel_result.process is not None
        ):
            active_tunnel.stop(tunnel_result.process)
        listener.close()
        raise
    return PreparedService(
        bootstrap=BootstrapResult(
            state=service_state,
            workspace_code=bootstrap.workspace_code,
            member_code=bootstrap.member_code,
        ),
        local_url=local_url,
        port=selected_port,
        socket=listener,
        tunnel=active_tunnel,
        tunnel_result=tunnel_result,
    )


def _cloudflared_path(discovered: str | None) -> Path | None:
    configured = os.environ.get("TRACE_AGENT_CLOUDFLARED")
    if configured is not None:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return None if discovered is None else Path(discovered)


def run_prepared_service(home: Path, prepared: PreparedService) -> None:
    try:
        with create_http_client() as http:
            application = create_service_app(home, build_production_runner(home, http))
            config = uvicorn.Config(
                application,
                host=prepared.bootstrap.state.host,
                port=prepared.port,
                access_log=False,
            )
            server = _WorkspaceServer(config)
            previous_handlers = {
                signal.SIGINT: signal.getsignal(signal.SIGINT),
                signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            }

            def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
                server.should_exit = True

            _ = signal.signal(signal.SIGINT, request_shutdown)
            _ = signal.signal(signal.SIGTERM, request_shutdown)
            try:
                server.run(sockets=[prepared.socket])
            finally:
                _ = signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
                _ = signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])
    finally:
        prepared.socket.close()
        if (
            prepared.tunnel is not None
            and prepared.tunnel_result is not None
            and prepared.tunnel_result.process is not None
        ):
            prepared.tunnel.stop(prepared.tunnel_result.process)
        ServiceStateStore(home).save(
            prepared.bootstrap.state.model_copy(update={"public_url": None})
        )


def attach_automation_worker(
    app: FastAPI,
    automation_worker: AutomationServiceWorker,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with create_task_group() as task_group:
            _ = task_group.start_soon(automation_worker.run)
            try:
                yield
            finally:
                task_group.cancel_scope.cancel()

    app.router.lifespan_context = lifespan
    return app


def create_service_app(
    home: Path,
    runner: GenerateOnePort,
    *,
    worker_config: ServiceWorkerConfig | None = None,
) -> FastAPI:
    return attach_automation_worker(
        create_app(home),
        create_service_worker(home, runner, config=worker_config),
    )
