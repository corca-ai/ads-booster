from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Final

import typer

from trace_capture.agent.control import AgentControl
from trace_capture.agent.factory import (
    AgentSessionConfig,
    build_agent_session,
    build_tool_context,
)
from trace_capture.agent.memory import JsonlMemoryStore, NullMemoryStore
from trace_capture.agent.repl import Repl
from trace_capture.agent.session_store import JsonSessionStore
from trace_capture.agent.tui import TraceAgentTui
from trace_capture.agent.tui_approval import TuiApproval
from trace_capture.auth.browser import BrowserOAuthError, BrowserOAuthOptions
from trace_capture.auth.codex import (
    CodexOAuth,
    DeviceChallenge,
    OAuthError,
    OAuthLoginOptions,
)
from trace_capture.auth.store import AuthStore, AuthStoreError
from trace_capture.cli.generate import app as generate_one_app
from trace_capture.config.settings import AgentSettings
from trace_capture.providers.codex import CodexResponsesClient
from trace_capture.service.cli import serve, service_app, workspace_app
from trace_capture.tools.approval import InteractiveApproval
from trace_capture.transport.http import create_http_client

app = typer.Typer(add_completion=False, no_args_is_help=False)
auth_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(auth_app, name="auth")
app.add_typer(generate_one_app, name="generate-one")
app.add_typer(workspace_app, name="workspace")
app.add_typer(service_app, name="service")
_ = app.command("serve")(serve)
DEFAULT_WORKSPACE: Final = Path()
DEFAULT_LOGIN_TIMEOUT: Final = 900.0


@app.callback(invoke_without_command=True)
def main(
    context: typer.Context,
    workspace: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_WORKSPACE,
    model: Annotated[str | None, typer.Option()] = None,
    plain: Annotated[bool, typer.Option()] = False,
) -> None:
    if context.invoked_subcommand is not None:
        return
    settings = AgentSettings.from_environment(workspace=workspace)
    if model is not None:
        settings = replace(settings, model=model)
    _run_agent(settings, plain=plain or not sys.stdin.isatty() or not sys.stdout.isatty())


@auth_app.command("login")
def login(
    no_browser: Annotated[bool, typer.Option()] = False,
    device_code: Annotated[bool, typer.Option()] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option(min=60.0, max=1_800.0),
    ] = DEFAULT_LOGIN_TIMEOUT,
) -> None:
    store = AuthStore.default()

    def on_challenge(challenge: DeviceChallenge) -> None:
        typer.echo(f"Open: {challenge.verification_url}")
        typer.echo(f"Code: {challenge.user_code}")

    def on_auth(url: str) -> None:
        typer.echo(f"Open: {url}")

    try:
        with create_http_client() as http:
            oauth = CodexOAuth(http=http, store=store)
            if device_code:
                _ = oauth.login(
                    options=OAuthLoginOptions(
                        open_browser=not no_browser,
                        timeout_seconds=timeout_seconds,
                    ),
                    on_challenge=on_challenge,
                )
            else:
                _ = oauth.login_browser(
                    options=BrowserOAuthOptions(
                        open_browser=not no_browser,
                        timeout_seconds=timeout_seconds,
                        input_fn=input,
                    ),
                    on_auth=on_auth,
                )
            typer.echo("OpenAI ChatGPT/Codex OAuth login complete.")
    except (BrowserOAuthError, OAuthError, AuthStoreError) as error:
        typer.echo(f"OAuth login failed: {error}", err=True)
        raise typer.Exit(code=1) from error


@auth_app.command("status")
def status() -> None:
    try:
        credential = AuthStore.default().load()
    except AuthStoreError as error:
        typer.echo(f"OAuth status unavailable: {error}", err=True)
        raise typer.Exit(code=1) from error
    if credential is None:
        typer.echo("OpenAI ChatGPT/Codex OAuth: not logged in")
        return
    account = credential.account_id or "unreported"
    details = f"account={account}, expires_at={credential.expires_at:.0f}"
    typer.echo(f"OpenAI ChatGPT/Codex OAuth: logged in ({details})")


@auth_app.command("logout")
def logout() -> None:
    try:
        AuthStore.default().clear()
    except AuthStoreError as error:
        typer.echo(f"OAuth logout failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("OpenAI ChatGPT/Codex OAuth credentials cleared.")


def _run_agent(settings: AgentSettings, *, plain: bool) -> None:
    with create_http_client() as http:
        store = AuthStore.default()
        oauth = CodexOAuth(http=http, store=store)
        client = CodexResponsesClient(http=http, oauth=oauth, model=settings.model)
        if plain:
            approval = InteractiveApproval(input_fn=input, output_fn=typer.echo)
            session = build_agent_session(
                AgentSessionConfig(
                    settings=settings,
                    client=client,
                    context=build_tool_context(settings, approval, http),
                    memory_store=(
                        JsonlMemoryStore(settings.memory_file)
                        if settings.memory_file is not None
                        else NullMemoryStore()
                    ),
                )
            )
            Repl(session=session, session_store=JsonSessionStore(settings.sessions_dir)).run()
            return
        approval = TuiApproval()
        session = build_agent_session(
            AgentSessionConfig(
                settings=settings,
                client=client,
                context=build_tool_context(settings, approval, http),
                memory_store=(
                    JsonlMemoryStore(settings.memory_file)
                    if settings.memory_file is not None
                    else NullMemoryStore()
                ),
            )
        )
        runtime = AgentControl(
            settings=settings,
            oauth=oauth,
            client=client,
            session=session,
        )
        TraceAgentTui(
            session=session,
            approval=approval,
            runtime=runtime,
            session_store=JsonSessionStore(settings.sessions_dir),
        ).run()
