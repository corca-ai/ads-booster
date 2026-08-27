# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Final

import httpx2
import typer

from ads_booster.service.launchd import (
    LaunchdConfig,
    bootstrap_launchd_service,
    default_plist_path,
    install_plist,
    launchd_label,
    stop_launchd_service,
)
from ads_booster.service.readiness import (
    discovered_cloudflared_path,
    wait_for_service_ready,
)
from ads_booster.service.runtime import (
    ServiceBindError,
    TunnelName,
    prepare_service,
    run_prepared_service,
)
from ads_booster.service.state import (
    ServiceStateError,
    ServiceStateStore,
    ensure_workspace,
)
from ads_booster.transport.http import create_http_client
from ads_booster.workspace import (
    MemberId,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    WorkspaceId,
)
from ads_booster.workspace.database import default_agent_home

workspace_app = typer.Typer(add_completion=False, no_args_is_help=True)
service_app = typer.Typer(add_completion=False, no_args_is_help=True)
_DEFAULT_HOST: Final = "127.0.0.1"
_DEFAULT_PORT: Final = 8765
_MAX_MEMBER_NAME_LENGTH: Final = 80
_HTTP_OK: Final = 200
_ACCESS_ID_SEPARATOR: Final = "%"


def _access_hint() -> None:
    typer.echo("Run `trace-agent workspace access` to display login details; startup hides codes.")


def _compose_workspace_access_id(
    workspace_id: WorkspaceId,
    member_id: MemberId,
    workspace_code: str,
    member_code: str,
) -> str:
    return _ACCESS_ID_SEPARATOR.join((workspace_id, member_id, workspace_code, member_code))


def serve(
    host: Annotated[
        str,
        typer.Option(help="Loopback host for the workspace service."),
    ] = _DEFAULT_HOST,
    port: Annotated[int, typer.Option(min=0, max=65_535)] = _DEFAULT_PORT,
    tunnel: Annotated[
        TunnelName,
        typer.Option(help="Tunnel provider: none or cloudflared."),
    ] = TunnelName.CLOUDFLARED,
    workspace_name: Annotated[str | None, typer.Option()] = None,
) -> None:
    try:
        prepared = prepare_service(
            default_agent_home(),
            host=host,
            port=port,
            tunnel_name=tunnel,
            workspace_name=workspace_name,
        )
    except (ServiceBindError, ServiceStateError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if prepared.tunnel_result is not None and prepared.tunnel_result.public_url is not None:
        typer.echo(f"Team URL: {prepared.tunnel_result.public_url}")
    else:
        typer.echo(f"Local URL: {prepared.local_url}")
    _access_hint()
    if prepared.tunnel_result is None:
        typer.echo("Public URL: unavailable (no tunnel provider configured)")
    elif prepared.tunnel_result.public_url is None:
        typer.echo(f"Public URL: unavailable ({prepared.tunnel_result.detail})")
    else:
        typer.echo(f"Public URL: {prepared.tunnel_result.public_url}")
    run_prepared_service(default_agent_home(), prepared)


@workspace_app.command("show")
def workspace_show() -> None:
    state = ServiceStateStore(default_agent_home()).load()
    if state is None:
        typer.echo("Workspace is not initialized. Run trace-agent serve first.")
        return
    workspace = SqliteWorkspaceStore(default_agent_home()).get_workspace(state.workspace_id)
    typer.echo(f"Workspace: {workspace.name}")
    typer.echo(f"Workspace ID: {state.workspace_id}")
    typer.echo(f"Member ID: {state.member_id}")
    typer.echo(f"Team URL: {state.public_url or 'unavailable until the service is running'}")
    typer.echo(f"Host URL: http://{state.host}:{state.port}")
    typer.echo("Access codes: hidden; run `trace-agent workspace access` to display fresh codes")


@workspace_app.command("add-member")
def workspace_add_member(
    name: Annotated[
        str,
        typer.Option("--name", help="Display name for the member."),
    ],
) -> None:
    state = ServiceStateStore(default_agent_home()).load()
    if state is None:
        typer.echo("Workspace is not initialized. Run trace-agent serve first.", err=True)
        raise typer.Exit(code=1)
    if not 1 <= len(name) <= _MAX_MEMBER_NAME_LENGTH:
        typer.echo("Member name must contain 1 to 80 characters.", err=True)
        raise typer.Exit(code=2)
    store = SqliteWorkspaceStore(default_agent_home())
    try:
        _ = store.get_workspace(state.workspace_id)
        _ = store.get_member(state.workspace_id, state.member_id)
        provisioned = store.create_member(state.workspace_id, name)
    except ScopedRecordNotFoundError as error:
        typer.echo(f"Workspace state is stale: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Member invite code (shown once; not written to logs):")
    typer.echo(f"Member ID: {provisioned.member.member_id}")
    typer.echo(f"Display name: {provisioned.member.display_name}")
    typer.echo(f"Member code: {provisioned.invite_code}")


def _rotate_owner_access() -> None:
    state = ServiceStateStore(default_agent_home()).load()
    if state is None:
        typer.echo("Workspace is not initialized. Run trace-agent serve first.", err=True)
        raise typer.Exit(code=1)
    store = SqliteWorkspaceStore(default_agent_home())
    workspace = store.get_workspace(state.workspace_id)
    member = store.get_member(state.workspace_id, state.member_id)
    try:
        rotated_workspace = store.rotate_workspace_code(
            state.workspace_id, expected_version=workspace.code_version
        )
        rotated_member = store.rotate_member_code(
            state.workspace_id,
            state.member_id,
            expected_version=member.code_version,
        )
    except RevisionConflictError as error:
        typer.echo(f"Code rotation conflicted with another operator: {error}", err=True)
        raise typer.Exit(code=1) from error
    access_id = _compose_workspace_access_id(
        rotated_workspace.workspace.workspace_id,
        rotated_member.member.member_id,
        rotated_workspace.access_code,
        rotated_member.invite_code,
    )
    typer.echo(f"Workspace access ID (shown once; not written to logs): {access_id}")


@workspace_app.command("access")
def workspace_access() -> None:
    _rotate_owner_access()


@workspace_app.command("rotate-code")
def rotate_code() -> None:
    _rotate_owner_access()


@workspace_app.command("start")
def workspace_start(
    port: Annotated[int, typer.Option(min=1, max=65_535)] = _DEFAULT_PORT,
    tunnel: Annotated[TunnelName, typer.Option()] = TunnelName.CLOUDFLARED,
    workspace_name: Annotated[str | None, typer.Option()] = None,
) -> None:
    service_install(
        port=port,
        tunnel=tunnel,
        workspace_name=workspace_name,
        plist=None,
        load=True,
    )


@service_app.command("status")
def service_status() -> None:
    home = default_agent_home()
    state = ServiceStateStore(home).load()
    plist_path = default_plist_path()
    typer.echo(f"launchd: {'installed' if plist_path.exists() else 'not installed'}")
    if state is None:
        typer.echo("runtime: not configured")
        return
    local_url = f"http://{state.host}:{state.port}"
    try:
        with create_http_client() as http:
            response = http.get(f"{local_url}/health", {})
        runtime = "running" if response.status_code == _HTTP_OK else "unhealthy"
    except httpx2.HTTPError:
        runtime = "stopped or unreachable"
    typer.echo(f"runtime: {runtime}")
    public_url = (
        state.public_url
        if runtime == "running" and state.tunnel == TunnelName.CLOUDFLARED.value
        else None
    )
    if public_url is None:
        typer.echo("public URL: unavailable (no live tunnel URL)")
    else:
        typer.echo(f"public URL: {public_url}")
    typer.echo(f"local URL: {local_url}")


@service_app.command("install")
def service_install(
    port: Annotated[int, typer.Option(min=1, max=65_535)] = _DEFAULT_PORT,
    tunnel: Annotated[TunnelName, typer.Option()] = TunnelName.CLOUDFLARED,
    workspace_name: Annotated[str | None, typer.Option()] = None,
    plist: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
    load: Annotated[bool, typer.Option("--load/--no-load")] = True,
) -> None:
    home = default_agent_home()
    try:
        _ = ensure_workspace(
            SqliteWorkspaceStore(home),
            ServiceStateStore(home),
            workspace_name=workspace_name,
        )
    except ServiceStateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _access_hint()
    executable = shutil.which("trace-agent")
    if executable is None:
        typer.echo("trace-agent executable is not available on PATH", err=True)
        raise typer.Exit(code=1)
    target = default_plist_path() if plist is None else plist.expanduser().resolve()
    install_plist(
        LaunchdConfig(
            Path(executable),
            home,
            _DEFAULT_HOST,
            port,
            tunnel.value,
            cloudflared_path=discovered_cloudflared_path(tunnel),
        ),
        target,
    )
    typer.echo(f"launchd plist: {target}")
    if not load:
        typer.echo("launchd load skipped; service definition generated only")
        return
    domain = f"gui/{os.getuid()}"
    if not stop_launchd_service(domain, launchd_label()):
        typer.echo("launchd service did not finish stopping; retry workspace start", err=True)
        raise typer.Exit(code=1)
    loaded = bootstrap_launchd_service(domain, target)
    if loaded.returncode != 0:
        detail = loaded.stderr.strip() or loaded.stdout.strip()
        typer.echo(
            f"launchd bootstrap failed: {detail or 'inspect the protected service stderr log'}",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo("launchd service installed and started")
    local_url, public_url = wait_for_service_ready(home, tunnel)
    if public_url is not None:
        typer.echo(f"Team URL: {public_url}")
    elif local_url is not None:
        typer.echo(f"Local URL: {local_url}")
        typer.echo("Public URL: unavailable; run `trace-agent service status` to retry")
    else:
        typer.echo("Service is starting; run `trace-agent service status` for its URL")
