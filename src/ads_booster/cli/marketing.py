"""On-premises service, research and knowledge CLI composition."""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, Annotated, cast

import typer
from pydantic import ValidationError

from ads_booster.cli.knowledge import app as knowledge_app
from ads_booster.cli.server import app as server_app
from ads_booster.knowledge.configuration import KnowledgeSettings, validate_settings
from ads_booster.knowledge.maintenance import inspect_owner
from ads_booster.marketing.agent_service.channel_setup import (
    browser_from_env,
    run_slack_worker,
    run_web_jobs,
    slack_from_env,
)
from ads_booster.marketing.agent_service.github_issues import token_from_env
from ads_booster.marketing.agent_service.http_api import (
    MarketingAgentApi,
    serve_marketing_agent_api,
)
from ads_booster.marketing.agent_service.image_edit_setup import (
    connect_image_edit,
    run_image_edit_worker,
)
from ads_booster.marketing.agent_service.integrations import AgentServiceIntegrationConfig
from ads_booster.marketing.agent_service.jobs import AgentJobs
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_knowledge_runtime,
    build_installed_marketing_agent_service,
)
from ads_booster.marketing.agent_service.maintenance import MaintenanceGate
from ads_booster.marketing.agent_service.oauth import OAuthTokenIntrospector
from ads_booster.marketing.agent_service.scheduler import (
    AgentSkillScheduler,
    DailySkillSchedule,
)
from ads_booster.marketing.agent_service.web_search import SearchInput
from ads_booster.marketing.channels.slack_events import events_from_env
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchError,
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchRunner,
)
from ads_booster.marketing.evidence_research_operator import EvidenceResearchOperatorError
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable

if TYPE_CHECKING:
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.transport.json_types import JsonObject

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_DYNAMIC_RESEARCH_REQUEST_MAX_BYTES = 1024 * 1024

app = typer.Typer(
    no_args_is_help=True,
    help="Operate the on-premises Marketing Agent Service.",
    pretty_exceptions_show_locals=False,
)
agent_app = typer.Typer(no_args_is_help=True, help="Run bounded Marketing OS reasoning sessions.")
service_app = typer.Typer(
    no_args_is_help=True,
    help="Operate the canonical on-premises Marketing Agent Service.",
)
app.add_typer(agent_app, name="agent")
app.add_typer(service_app, name="service")
app.add_typer(server_app, name="server")
app.add_typer(knowledge_app, name="knowledge")


@app.command("version")
def version_command(
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Print the installed trace-marketing package version."""
    try:
        package_version = version("trace-appium-capture")
    except PackageNotFoundError:
        package_version = "source"
    if output_json:
        typer.echo(json.dumps({"version": package_version}))
    else:
        typer.echo(package_version)


@service_app.command("doctor")
def service_doctor(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    """Report local service readiness without creating state."""
    executable = resolve_codex_executable()
    paths = InstalledServicePaths(_home(home) / "marketing-agent" / "service")
    try:
        knowledge_settings = KnowledgeSettings.from_env(os.environ)
        if knowledge_settings.enabled:
            validate_settings(knowledge_settings)
            root, _, _ = knowledge_settings.require_enabled()
            owner = inspect_owner(root)
            knowledge_state = f"configured:{owner.state}"
        else:
            knowledge_state = "disabled"
    except ValueError as error:
        knowledge_state = str(error)
    typer.echo(
        json.dumps(
            {
                "schema_version": "trace.marketing-agent-service-doctor.v1",
                "canonical_run_owner": "on_prem_marketing_agent_service",
                "state_root": str(paths.root),
                "reasoning_provider": "official-codex-cli",
                "reasoning_ready": executable is not None,
                "appium_required": False,
                "knowledge": knowledge_state,
                "ready": executable is not None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@service_app.command("run")
def service_run(  # noqa: C901,PLR0912,PLR0913,PLR0915,PLR0917 - explicit optional worker lifecycle.
    model: Annotated[str, typer.Option(help="Pinned Codex reasoning model.")],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    host: Annotated[
        str, typer.Option(help="Bind address; remote binds require OAuth.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8090,
    tenant: Annotated[str, typer.Option(help="Tenant bound to this service token.")] = "trace",
    principal: Annotated[
        str, typer.Option(help="Principal bound to approval decisions from this token.")
    ] = "local-operator",
    timeout_seconds: Annotated[
        float, typer.Option(min=30.0, max=1800.0, help="Per-reasoning-turn timeout.")
    ] = 300.0,
) -> None:
    """Run the always-on canonical Agent API and configured service tools."""
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install Codex CLI and run `codex login`"
        raise typer.BadParameter(message)
    slack_only = os.environ.get("TRACE_MARKETING_SLACK_ONLY") == "1"
    gate_path = os.environ.get("TRACE_MARKETING_MAINTENANCE_FILE")
    gate = MaintenanceGate(
        Path(gate_path) if gate_path else None,
        os.environ.get("TRACE_MARKETING_RELEASE", "unmanaged"),
    )
    introspection_url = os.environ.get("TRACE_MARKETING_OAUTH_INTROSPECTION_URL")
    oauth = None
    if introspection_url:
        oauth = OAuthTokenIntrospector(
            introspection_url=introspection_url,
            client_id=_required("TRACE_MARKETING_OAUTH_CLIENT_ID"),
            client_secret=_required("TRACE_MARKETING_OAUTH_CLIENT_SECRET"),
            audience=_required("TRACE_MARKETING_OAUTH_AUDIENCE"),
            tenant_claim=os.environ.get("TRACE_MARKETING_OAUTH_TENANT_CLAIM", "workspace_id"),
        )
    token = os.environ.get("TRACE_MARKETING_SERVICE_TOKEN", "")
    if oauth is None and not token and not slack_only:
        token = _required("TRACE_MARKETING_SERVICE_TOKEN")
    paths = InstalledServicePaths(_home(home) / "marketing-agent" / "service")
    paths.prepare()
    knowledge_settings = KnowledgeSettings.from_env(os.environ)
    knowledge_runtime = None
    if knowledge_settings.enabled:
        knowledge_runtime = build_installed_knowledge_runtime(
            settings=knowledge_settings,
            service_database=paths.database,
            codex=CodexCli(executable=executable, model=model),
            model_id=model,
        )
    service = build_installed_marketing_agent_service(
        paths=paths,
        codex_executable=executable,
        model_id=model,
        timeout_seconds=timeout_seconds,
        integrations=AgentServiceIntegrationConfig(
            github_token=token_from_env(os.environ),
            slack_bot_token=os.environ.get("TRACE_MARKETING_SLACK_BOT_TOKEN"),
            slack_channel_id=os.environ.get("TRACE_MARKETING_SLACK_CHANNEL_ID"),
            notion_token=os.environ.get("TRACE_MARKETING_NOTION_TOKEN"),
            notion_parent_page_id=os.environ.get("TRACE_MARKETING_NOTION_PARENT_PAGE_ID"),
        ),
        knowledge=None if knowledge_runtime is None else knowledge_runtime.adapter,
    )
    browser_login = browser_from_env(os.environ, oauth)
    slack_commands = slack_from_env(os.environ, service, tenant_id=tenant)
    slack_events = events_from_env(os.environ, slack_commands)

    def notify_image_completion(tenant_id: str, run_id: str, event_id: str) -> None:
        if slack_events is not None:
            _ = slack_events.enqueue_run_update(tenant_id, run_id, event_id=event_id)

    image_edit_path = os.environ.get("TRACE_MARKETING_IMAGE_EDIT_CONFIG")
    image_edit = (
        None
        if image_edit_path is None
        else connect_image_edit(
            service,
            config_path=Path(image_edit_path),
            now=datetime.now(UTC),
            on_completed=notify_image_completion,
        )
    )
    if slack_only and slack_commands is None:
        message = "Slack-only mode requires a configured Slack installation"
        raise typer.BadParameter(message)
    if os.environ.get("TRACE_MARKETING_PUBLIC_ORIGIN") and oauth is None and not slack_only:
        message = "Public service origin requires OAuth authentication"
        raise typer.BadParameter(message)
    scheduler = _configured_daily_scheduler(service, tenant=tenant, principal=principal)
    scheduler_stop = Event()
    scheduler_thread = None
    if scheduler is not None:
        scheduler_thread = Thread(
            target=_run_skill_scheduler,
            args=(scheduler, scheduler_stop, gate),
            name="trace-marketing-skill-scheduler",
            daemon=True,
        )
    jobs = AgentJobs(service)
    jobs_thread = Thread(
        target=run_web_jobs,
        args=(jobs, scheduler_stop, gate),
        name="trace-marketing-web-jobs",
        daemon=True,
    )
    slack_thread = (
        None
        if slack_commands is None
        else Thread(
            target=run_slack_worker,
            args=(slack_commands, scheduler_stop, gate, slack_events),
            name="trace-marketing-slack",
            daemon=True,
        )
    )
    knowledge_thread = (
        None
        if knowledge_runtime is None
        else Thread(
            target=knowledge_runtime.runtime.run_continuous,
            name="trace-marketing-knowledge",
            daemon=True,
        )
    )

    image_edit_thread = (
        None
        if image_edit is None
        else Thread(
            target=run_image_edit_worker,
            args=(image_edit, scheduler_stop, gate),
            name="trace-marketing-image-edit",
            daemon=True,
        )
    )

    def start_background() -> None:
        if image_edit_thread is not None:
            image_edit_thread.start()
        jobs_thread.start()
        if scheduler_thread is not None:
            scheduler_thread.start()
        if slack_thread is not None:
            slack_thread.start()
        if knowledge_thread is not None:
            knowledge_thread.start()

    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def stop_service(_signum: int, _frame: object) -> None:
        scheduler_stop.set()
        if knowledge_runtime is not None:
            knowledge_runtime.runtime.request_stop()
        raise KeyboardInterrupt

    _ = signal.signal(signal.SIGTERM, stop_service)

    typer.echo(f"Marketing Agent Service listening on http://{host}:{port}")
    try:
        serve_marketing_agent_api(
            MarketingAgentApi(
                service=service,
                tenant_id=tenant,
                principal_id=principal,
                bearer_token=token,
                oauth_authenticator=oauth,
                browser_login=browser_login,
                slack_commands=slack_commands,
                slack_events=slack_events,
                jobs=jobs,
                allowed_tenant_id=tenant,
                slack_only=slack_only,
                maintenance=gate,
                knowledge_ingress=None
                if knowledge_runtime is None
                else knowledge_runtime.adapter.ingress,
                knowledge_transfers=None
                if knowledge_runtime is None
                else knowledge_runtime.adapter,
            ),
            host=host,
            port=port,
            on_started=start_background,
        )
    finally:
        _ = signal.signal(signal.SIGTERM, previous_sigterm)
        scheduler_stop.set()
        if image_edit_thread is not None and image_edit_thread.is_alive():
            image_edit_thread.join(timeout=5)
        if knowledge_runtime is not None:
            knowledge_runtime.runtime.request_stop()
        if jobs_thread.is_alive():
            jobs_thread.join(timeout=5)
        if scheduler_thread is not None and scheduler_thread.is_alive():
            scheduler_thread.join(timeout=5)
        if slack_thread is not None and slack_thread.is_alive():
            slack_thread.join(timeout=5)
        if knowledge_thread is not None and knowledge_thread.is_alive():
            knowledge_thread.join(timeout=15)
        if knowledge_runtime is not None:
            knowledge_runtime.runtime.close()


@agent_app.command("research")
def agent_research(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Immutable dynamic-research request JSON.",
        ),
    ],
    model: Annotated[
        str,
        typer.Option(help="Pinned official Codex model recorded in every planner receipt."),
    ],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    timeout_seconds: Annotated[
        float,
        typer.Option(min=30.0, max=1800.0, help="Per-Codex-turn timeout."),
    ] = 300.0,
) -> None:
    """Dynamically choose read-only evidence tools and emit a receipt-grounded brief."""
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install Codex CLI and log in"
        raise typer.BadParameter(message)
    try:
        request = _load_dynamic_research_request(input_path)
        result = DynamicEvidenceResearchRunner(
            codex=CodexCli(executable=executable, model=model),
            state_root=_home(home) / "marketing-agent" / "runtime",
            model_id=model,
            timeout_seconds=timeout_seconds,
        ).run(request)
    except ValidationError as error:
        typer.echo("dynamic_research_request_invalid", err=True)
        raise typer.Exit(code=2) from error
    except (EvidenceResearchOperatorError, OSError, UnicodeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(result.model_dump_json(indent=2))
    if result.state == "awaiting_reconciliation":
        raise typer.Exit(code=3)


def _load_dynamic_research_request(input_path: Path) -> DynamicEvidenceResearchRequest:
    return DynamicEvidenceResearchRequest.model_validate_json(
        _read_bounded_input(input_path, error="dynamic_research_request_too_large")
    )


def _configured_daily_scheduler(
    service: MarketingAgentService, *, tenant: str, principal: str
) -> AgentSkillScheduler | None:
    input_value = os.environ.get("TRACE_MARKETING_DAILY_RESEARCH_INPUT")
    if not input_value:
        return None
    input_path = Path(input_value).expanduser()
    if not input_path.is_file() or input_path.stat().st_size > _DYNAMIC_RESEARCH_REQUEST_MAX_BYTES:
        message = "TRACE_MARKETING_DAILY_RESEARCH_INPUT must be a bounded JSON file"
        raise typer.BadParameter(message)
    skill_id = os.environ.get("TRACE_MARKETING_DAILY_SKILL", "research.daily_slack")
    payload = cast("JsonObject", json.loads(input_path.read_text(encoding="utf-8")))
    context = payload if skill_id == "research.daily_slack_only" else {"research_request": payload}
    if skill_id == "research.daily_slack_only":
        context = SearchInput.model_validate(context).model_dump(mode="json")
    schedule_at = os.environ.get("TRACE_MARKETING_DAILY_AT", "08:00")
    try:
        hour_text, minute_text = schedule_at.split(":", maxsplit=1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as error:
        message = "TRACE_MARKETING_DAILY_AT must be HH:MM"
        raise typer.BadParameter(message) from error
    return AgentSkillScheduler(
        service,
        (
            DailySkillSchedule(
                skill_id=skill_id,
                tenant_id=os.environ.get("TRACE_MARKETING_DAILY_TENANT", tenant),
                principal_id=os.environ.get("TRACE_MARKETING_DAILY_PRINCIPAL", principal),
                timezone=os.environ.get("TRACE_MARKETING_DAILY_TIMEZONE", "Asia/Seoul"),
                hour=hour,
                minute=minute,
                context=cast("JsonObject", context),
            ),
        ),
    )


def _run_skill_scheduler(
    scheduler: AgentSkillScheduler, stop: Event, gate: MaintenanceGate | None = None
) -> None:
    gate = gate or MaintenanceGate()
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                if admitted:
                    _ = scheduler.tick(now=datetime.now(UTC))
        except Exception as error:  # noqa: BLE001 - scheduler survives one bounded run failure.
            typer.echo(f"scheduled skill deferred: {type(error).__name__}", err=True)
        _ = stop.wait(30)


def _read_bounded_input(input_path: Path, *, error: str) -> str:
    if input_path.stat().st_size > _DYNAMIC_RESEARCH_REQUEST_MAX_BYTES:
        raise DynamicEvidenceResearchError(error)
    return input_path.read_text(encoding="utf-8")


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
