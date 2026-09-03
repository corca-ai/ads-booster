"""# noqa: SIZE_OK - Worker lifecycle CLI composition root; commands share presentation helpers."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Annotated, Never, Protocol

import typer
from pydantic import ValidationError

from ads_booster.marketing.agent_service.http_api import (
    MarketingAgentApi,
    serve_marketing_agent_api,
)
from ads_booster.marketing.agent_service.launchd import (
    MarketingAgentLaunchd,
    default_service_plist_path,
)
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchError,
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchRunner,
)
from ads_booster.marketing.errors import CloudflareQueueError
from ads_booster.marketing.evidence_research_operator import EvidenceResearchOperatorError
from ads_booster.marketing.feature_launch_run import (
    FeatureLaunchRunError,
    FeatureLaunchRunner,
    FeatureLaunchRunRequest,
    HttpHostedCampaignControlPlane,
)
from ads_booster.marketing.hosted_candidate_judgment import HostedCandidateJudgmentExecutor
from ads_booster.marketing.hosted_creative_judgment import HostedCreativeJudgmentExecutor
from ads_booster.marketing.hosted_experiment_evaluation import HostedExperimentEvaluationExecutor
from ads_booster.marketing.hosted_feature_launch_run import HostedFeatureLaunchRunExecutor
from ads_booster.marketing.hosted_generation import HostedWorkspaceGenerationExecutor
from ads_booster.marketing.hosted_judgment import HostedMarketingJudgmentExecutor
from ads_booster.marketing.hosted_learning_judgment import HostedLearningJudgmentExecutor
from ads_booster.marketing.hosted_next_experiment_judgment import (
    HostedNextExperimentJudgmentExecutor,
)
from ads_booster.marketing.hosted_reassessment_judgment import HostedOutcomeReassessmentExecutor
from ads_booster.marketing.hosted_reference_research import HostedReferenceResearchExecutor
from ads_booster.marketing.hosted_task_router import PlanlessHostedTaskExecutor
from ads_booster.marketing.inbox import MarketingInbox
from ads_booster.marketing.native_capture import build_hosted_capture_executor
from ads_booster.marketing.worker_broker import (
    MacWorkerConfig,
    MacWorkerCredential,
    MacWorkerStore,
    WorkerBrokerClient,
    enroll_mac_worker,
    normalize_control_plane_origin,
)
from ads_booster.marketing.worker_doctor import (
    MacWorkerDoctorReport,
    inspect_mac_worker,
    installed_version,
)
from ads_booster.marketing.worker_events import QueuedWorkerEventReporter
from ads_booster.marketing.worker_launchd import (
    MacWorkerLaunchd,
    MacWorkerUpdaterLaunchd,
    default_updater_plist_path,
    default_worker_plist_path,
    kickstart_managed_updater,
)
from ads_booster.marketing.worker_loop import MarketingWorkerLoop
from ads_booster.marketing.worker_update import (
    GitHubArtifactAttestationVerifier,
    GitHubReleaseSource,
    HeartbeatReceiptStore,
    MacWorkerUpdater,
    ManagedReleaseInstaller,
    ManagedWorkerPaths,
    UpdateState,
    UpdateStateStore,
    WorkerRuntimeVerifier,
    WorkerUpdateError,
    current_installed_release,
    default_install_root,
    run_command,
    update_drain_requested,
)
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable
from ads_booster.transport.http import create_http_client

if TYPE_CHECKING:
    from ads_booster.marketing.hosted_task_router import PlanlessPrepared
    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonObject

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_DYNAMIC_RESEARCH_REQUEST_MAX_BYTES = 1024 * 1024

app = typer.Typer(no_args_is_help=True, help="Operate the dynamic marketing account loop.")
worker_app = typer.Typer(no_args_is_help=True, help="Enroll and operate a replaceable Mac worker.")
agent_app = typer.Typer(no_args_is_help=True, help="Run bounded Marketing OS reasoning sessions.")
service_app = typer.Typer(
    no_args_is_help=True,
    help="Operate the canonical on-premises Marketing Agent Service.",
)
app.add_typer(worker_app, name="worker")
app.add_typer(agent_app, name="agent")
app.add_typer(service_app, name="service")


@app.command("version")
def version_command(
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Print the installed trace-marketing package version."""
    package_version = installed_version()
    if output_json:
        typer.echo(json.dumps({"version": package_version}))
    else:
        typer.echo(package_version)


@service_app.command("doctor")
def service_doctor(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    """Report service readiness without requiring or inspecting Appium."""
    executable = resolve_codex_executable()
    paths = InstalledServicePaths(_home(home) / "marketing-agent" / "service")
    typer.echo(
        json.dumps(
            {
                "schema_version": "trace.marketing-agent-service-doctor.v1",
                "canonical_run_owner": "on_prem_marketing_agent_service",
                "state_root": str(paths.root),
                "reasoning_provider": "official-codex-cli",
                "reasoning_ready": executable is not None,
                "appium_required": False,
                "ready": executable is not None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@service_app.command("run")
def service_run(  # noqa: PLR0913,PLR0917 - operator-visible configuration stays explicit.
    model: Annotated[str, typer.Option(help="Pinned Codex reasoning model.")],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    host: Annotated[str, typer.Option(help="Loopback bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    tenant: Annotated[str, typer.Option(help="Tenant bound to this service token.")] = "trace",
    principal: Annotated[
        str, typer.Option(help="Principal bound to approval decisions from this token.")
    ] = "local-operator",
    timeout_seconds: Annotated[
        float, typer.Option(min=30.0, max=1800.0, help="Per-reasoning-turn timeout.")
    ] = 300.0,
) -> None:
    """Run the always-on canonical Agent API; Appium workers are optional tools."""
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install Codex CLI and run `codex login`"
        raise typer.BadParameter(message)
    token = _required("TRACE_MARKETING_SERVICE_TOKEN")
    paths = InstalledServicePaths(_home(home) / "marketing-agent" / "service")
    service = build_installed_marketing_agent_service(
        paths=paths,
        codex_executable=executable,
        model_id=model,
        timeout_seconds=timeout_seconds,
    )
    typer.echo(f"Marketing Agent Service listening on http://{host}:{port}")
    serve_marketing_agent_api(
        MarketingAgentApi(
            service=service,
            tenant_id=tenant,
            principal_id=principal,
            bearer_token=token,
        ),
        host=host,
        port=port,
    )


@service_app.command("daemon", hidden=True)
def service_daemon(
    model: Annotated[str, typer.Option(help="Pinned Codex reasoning model.")],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    tenant: Annotated[str, typer.Option()] = "trace",
    principal: Annotated[str, typer.Option()] = "local-operator",
) -> None:
    """Launchd entrypoint that reads the bearer token from its protected file."""
    root = _home(home)
    token_path = root / "marketing-agent" / "service-token"
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        message = "marketing agent service token is missing"
        raise typer.BadParameter(message) from error
    if not token:
        message = "marketing agent service token is empty"
        raise typer.BadParameter(message)
    os.environ["TRACE_MARKETING_SERVICE_TOKEN"] = token
    service_run(model=model, home=root, port=port, tenant=tenant, principal=principal)


@service_app.command("install")
def service_install(
    model: Annotated[str, typer.Option(help="Pinned Codex reasoning model.")],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    tenant: Annotated[str, typer.Option()] = "trace",
    principal: Annotated[str, typer.Option()] = "local-operator",
) -> None:
    """Install and start the canonical service as a per-user LaunchAgent."""
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install it and run `codex login`"
        raise typer.BadParameter(message)
    launchd = MarketingAgentLaunchd(
        executable=Path(sys.argv[0]).resolve(),
        agent_home=_home(home),
        plist_path=default_service_plist_path(),
        codex_executable=executable,
        model=model,
        port=port,
        tenant=tenant,
        principal=principal,
    )
    _ = launchd.stop()
    _ = launchd.install()
    result = launchd.start()
    if result.returncode != 0:
        typer.echo(result.stderr.strip() or "marketing agent service failed to start", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"installed: {launchd.plist_path}")
    typer.echo(f"listening: http://127.0.0.1:{port}")
    typer.echo(f"token: {launchd.token_path} (mode 0600; value not printed)")


@service_app.command("status")
def service_status(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    """Report whether the owned canonical service LaunchAgent is loaded."""
    executable = resolve_codex_executable() or Path("codex")
    launchd = MarketingAgentLaunchd(
        executable=Path(sys.argv[0]).resolve(),
        agent_home=_home(home),
        plist_path=default_service_plist_path(),
        codex_executable=executable,
        model="unused",
    )
    result = launchd.status()
    typer.echo(
        json.dumps({"installed": launchd.owns_installed_plist(), "running": result.returncode == 0})
    )
    if result.returncode != 0:
        raise typer.Exit(code=1)


@service_app.command("stop")
def service_stop(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    """Stop the canonical service without deleting its state or token."""
    executable = resolve_codex_executable() or Path("codex")
    launchd = MarketingAgentLaunchd(
        executable=Path(sys.argv[0]).resolve(),
        agent_home=_home(home),
        plist_path=default_service_plist_path(),
        codex_executable=executable,
        model="unused",
    )
    result = launchd.stop()
    if result.returncode != 0 and "Could not find service" not in result.stderr:
        typer.echo(result.stderr.strip(), err=True)
        raise typer.Exit(code=2)
    typer.echo("stopped")


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


@agent_app.command("launch")
def agent_launch(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Immutable feature-launch run request JSON.",
        ),
    ],
    url: Annotated[str, typer.Option(help="Hosted Trace control-plane HTTPS origin.")],
    model: Annotated[
        str,
        typer.Option(help="Pinned official Codex model recorded in research receipts."),
    ],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    timeout_seconds: Annotated[
        float,
        typer.Option(min=30.0, max=1800.0, help="Per-Codex-turn timeout."),
    ] = 300.0,
) -> None:
    """Research a feature, then safely hand one shadow campaign to the hosted loop."""
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install Codex CLI and log in"
        raise typer.BadParameter(message)
    token = _required("TRACE_MARKETING_CONTROL_TOKEN")
    agent_home = _home(home) / "marketing-agent"
    try:
        request = FeatureLaunchRunRequest.model_validate_json(
            _read_bounded_input(input_path, error="feature_launch_request_too_large")
        )
        with create_http_client() as http:
            result = FeatureLaunchRunner(
                research_runner=DynamicEvidenceResearchRunner(
                    codex=CodexCli(executable=executable, model=model),
                    state_root=agent_home / "runtime",
                    model_id=model,
                    timeout_seconds=timeout_seconds,
                ),
                control_plane=HttpHostedCampaignControlPlane(
                    http=http,
                    origin=_https_origin(url),
                    bearer_token=token,
                ),
                state_root=agent_home / "feature-launch",
            ).run(request)
    except ValidationError as error:
        typer.echo("feature_launch_request_invalid", err=True)
        raise typer.Exit(code=2) from error
    except (EvidenceResearchOperatorError, FeatureLaunchRunError, OSError, UnicodeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(result.model_dump_json(indent=2))
    if result.state == "awaiting_reconciliation":
        raise typer.Exit(code=3)
    if result.state == "blocked":
        raise typer.Exit(code=4)


def _load_dynamic_research_request(input_path: Path) -> DynamicEvidenceResearchRequest:
    return DynamicEvidenceResearchRequest.model_validate_json(
        _read_bounded_input(input_path, error="dynamic_research_request_too_large")
    )


def _read_bounded_input(input_path: Path, *, error: str) -> str:
    if input_path.stat().st_size > _DYNAMIC_RESEARCH_REQUEST_MAX_BYTES:
        raise DynamicEvidenceResearchError(error)
    return input_path.read_text(encoding="utf-8")


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class DoctorHeartbeat:
    """Mutable cache because concurrent heartbeat calls refresh one shared doctor report."""

    report: MacWorkerDoctorReport
    checked_at: float
    refresh_seconds: float = 30.0
    lock: Lock = field(default_factory=Lock)

    refreshing: bool = False

    def __call__(self) -> JsonObject:
        with self.lock:
            now = time.monotonic()
            if now - self.checked_at < self.refresh_seconds or self.refreshing:
                return self.report.heartbeat()
            self.refreshing = True

        refreshed: MacWorkerDoctorReport | None = None
        try:
            refreshed = inspect_mac_worker()
        finally:
            with self.lock:
                if refreshed is not None:
                    self.report = refreshed
                    self.checked_at = time.monotonic()
                self.refreshing = False
                current = self.report.heartbeat()
        return current


@worker_app.command("create-enrollment")
def worker_create_enrollment(
    url: Annotated[str, typer.Option(help="Deployed Cloudflare workspace origin.")],
    name: Annotated[str, typer.Option(help="Team-visible worker alias.")],
    pool: Annotated[str, typer.Option(help="Capability pool used for task routing.")] = "appium",
    ttl_seconds: Annotated[int, typer.Option(min=60, max=3600)] = 600,
) -> None:
    """Create a single-use enrollment code without exposing the control token to the Mac."""
    payload = _admin_post(
        url,
        "/v1/worker-enrollments",
        {"display_name": name, "pool": pool, "ttl_seconds": ttl_seconds},
    )
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@worker_app.command("enroll")
def worker_enroll(
    url: Annotated[str, typer.Option(help="Deployed Cloudflare workspace origin.")],
    code: Annotated[str, typer.Option(help="Single-use code from create-enrollment.")],
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    poll_seconds: Annotated[float, typer.Option(min=0.5, max=60.0)] = 2.0,
) -> None:
    """Bind this Mac to a revocable machine identity stored outside macOS Keychain."""
    report = inspect_mac_worker()
    try:
        with create_http_client() as http:
            config, credential = enroll_mac_worker(
                http,
                control_plane_url=_https_origin(url),
                enrollment_code=code,
                heartbeat=report.heartbeat(),
                poll_seconds=poll_seconds,
            )
        store = MacWorkerStore(_home(home))
        store.save(config, credential)
    except (CloudflareQueueError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"enrolled: {config.display_name} ({config.worker_id})")
    typer.echo(f"credential: {store.credential_path} (mode 0600, no Keychain binding)")
    typer.echo(f"doctor: {report.summary}")


@worker_app.command("doctor")
def worker_doctor() -> None:
    """Inspect the native Appium boundary without claiming a remote task."""
    report = inspect_mac_worker()
    typer.echo(
        json.dumps(
            {
                "ready": report.ready,
                "summary": report.summary,
                "version": report.version,
                "checks": report.checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report.ready:
        raise typer.Exit(code=1)


@worker_app.command("run")
def worker_run(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    once: Annotated[bool, typer.Option(help="Poll and process at most one task.")] = False,
) -> None:
    """Run the D1-backed worker in the foreground."""
    _run_mac_worker(_home(home), once=once)


@worker_app.command("service", hidden=True)
def worker_service() -> None:
    """Stable launchd entrypoint using the enrolled machine credential file."""
    _run_mac_worker(_home(None), once=False)


@worker_app.command("install-service")
def worker_install_service(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    start: Annotated[bool, typer.Option("--start/--no-start")] = True,
) -> None:
    """Install a per-user LaunchAgent whose plist contains no worker credential."""
    launchd = _worker_launchd(_home(home), require_codex=True)
    _stop_worker_launchd(launchd)
    launchd.install()
    typer.echo(f"plist: {launchd.plist_path}")
    if not start:
        return
    result = launchd.start()
    if result.returncode != 0:
        typer.echo(_process_error("launchctl bootstrap failed", result), err=True)
        raise typer.Exit(code=1)
    typer.echo("worker service: running")


@worker_app.command("start")
def worker_start(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    launchd = _worker_launchd(_home(home))
    if not launchd.plist_path.is_file():
        typer.echo("worker plist is not installed; run worker install-service", err=True)
        raise typer.Exit(code=1)
    result = launchd.start()
    if result.returncode != 0:
        typer.echo(_process_error("launchctl bootstrap failed", result), err=True)
        raise typer.Exit(code=1)
    typer.echo("worker service: running")


@worker_app.command("stop")
def worker_stop(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    _stop_worker_launchd(_worker_launchd(_home(home)))
    typer.echo("worker service: stopped")


@worker_app.command("restart")
def worker_restart(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    launchd = _worker_launchd(_home(home))
    _stop_worker_launchd(launchd)
    if not launchd.plist_path.is_file():
        typer.echo("worker plist is not installed; run worker install-service", err=True)
        raise typer.Exit(code=1)
    result = launchd.start()
    if result.returncode != 0:
        typer.echo(_process_error("launchctl bootstrap failed", result), err=True)
        raise typer.Exit(code=1)
    typer.echo("worker service: restarted")


@worker_app.command("status")
def worker_status(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    root = _home(home)
    launchd = _worker_launchd(root)
    result = launchd.status()
    try:
        config, _credential = MacWorkerStore(root).load()
        enrollment: JsonObject = {
            "worker_id": config.worker_id,
            "display_name": config.display_name,
            "pool": config.pool,
            "control_plane_url": config.control_plane_url,
        }
    except CloudflareQueueError:
        enrollment = {"state": "not_enrolled"}
    pinned_codex = launchd.installed_codex_executable()
    report = inspect_mac_worker(codex_executable=pinned_codex, resolve_codex=False)
    typer.echo(
        json.dumps(
            {
                "service": "running" if result.returncode == 0 else "stopped",
                "plist": str(launchd.plist_path),
                "codex_runtime": {
                    "source": "launchagent",
                    "executable": str(pinned_codex) if pinned_codex is not None else None,
                },
                "enrollment": enrollment,
                "doctor": {"ready": report.ready, "summary": report.summary},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@worker_app.command("uninstall-service")
def worker_uninstall_service(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    launchd = _worker_launchd(_home(home))
    _stop_worker_launchd(launchd)
    if launchd.plist_path.exists():
        launchd.plist_path.unlink()
    typer.echo("worker service: uninstalled; enrollment credential was preserved")


@worker_app.command("update")
def worker_update(  # noqa: PLR0913, PLR0917 - explicit operator CLI surface.
    apply: Annotated[
        bool,
        typer.Option("--apply/--dry-run", help="Apply or only inspect the latest release."),
    ] = False,
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    install_root: Annotated[
        Path | None,
        typer.Option(help="Managed versioned release root."),
    ] = None,
    uv: Annotated[Path | None, typer.Option(help="Pinned uv executable used for staging.")] = None,
    gh: Annotated[
        Path | None,
        typer.Option(help="Pinned GitHub CLI executable used for artifact attestation."),
    ] = None,
    drain_timeout_seconds: Annotated[
        float,
        typer.Option(min=0.0, max=3600.0, help="Maximum local drain wait."),
    ] = 900.0,
) -> None:
    """Inspect or safely apply the latest verified stable GitHub Release."""
    _require_macos()
    paths = _managed_paths(_home(home), install_root)
    try:
        uv_executable = _resolve_uv(uv) if apply else Path("/nonexistent/uv")
        gh_executable = _resolve_gh(gh)
        with create_http_client() as http:
            updater = _mac_worker_updater(
                paths,
                http=http,
                uv=uv_executable,
                gh=gh_executable,
                drain_timeout_seconds=drain_timeout_seconds,
            )
            attempt = updater.apply() if apply else updater.inspect()
    except WorkerUpdateError as error:
        typer.echo(json.dumps({"status": "failed", "reason": str(error)}), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(attempt.as_json(), ensure_ascii=False, indent=2))
    if attempt.status in {"failed", "rolled_back", "rollback_failed"}:
        raise typer.Exit(code=1)


@worker_app.command("install-updater")
def worker_install_updater(  # noqa: PLR0913, PLR0917 - explicit operator CLI surface.
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    install_root: Annotated[
        Path | None,
        typer.Option(help="Managed versioned release root."),
    ] = None,
    uv: Annotated[Path | None, typer.Option(help="Pinned uv executable used for staging.")] = None,
    gh: Annotated[
        Path | None,
        typer.Option(help="Pinned GitHub CLI executable used for artifact attestation."),
    ] = None,
    interval_seconds: Annotated[int, typer.Option(min=300, max=86400)] = 3600,
    start: Annotated[bool, typer.Option("--start/--no-start")] = True,
) -> None:
    """Install the updater as a separate per-user LaunchAgent."""
    _require_macos()
    paths = _managed_paths(_home(home), install_root)
    try:
        release = current_installed_release(paths)
        worker = _managed_worker_launchd(paths)
        codex = worker.installed_codex_executable()
        if codex is None or not codex.is_file():
            _update_failure("worker LaunchAgent has no valid pinned Codex executable")
        updater = _updater_launchd(
            paths,
            codex=codex,
            uv=_resolve_uv(uv),
            gh=_resolve_gh(gh),
            interval_seconds=interval_seconds,
        )
        _ = updater.stop()
        if not updater.wait_until_stopped():
            _update_failure("updater LaunchAgent did not finish stopping")
        updater.install()
        if start and updater.start().returncode != 0:
            _update_failure("updater LaunchAgent failed to start")
    except (OSError, ValueError, WorkerUpdateError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"updater plist: {updater.plist_path}")
    typer.echo(f"managed release: {release.version}")


@worker_app.command("updater-status")
def worker_updater_status(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    install_root: Annotated[
        Path | None,
        typer.Option(help="Managed versioned release root."),
    ] = None,
) -> None:
    """Report sanitized managed-release and updater state."""
    _require_macos()
    paths = _managed_paths(_home(home), install_root)
    current: JsonObject | None = None
    try:
        release = current_installed_release(paths)
        current = {
            "version": release.version,
            "tag": release.receipt.tag,
            "commit_sha": release.receipt.commit_sha,
            "bundle_sha256": release.receipt.bundle.sha256,
        }
    except WorkerUpdateError:
        current = None
    worker = _managed_worker_launchd(paths)
    codex = worker.installed_codex_executable() or Path("/nonexistent/codex")
    updater = _updater_launchd(
        paths,
        codex=codex,
        uv=Path("/nonexistent/uv"),
        gh=Path("/nonexistent/gh"),
        interval_seconds=3600,
    )
    state = UpdateStateStore(paths.state).load()
    typer.echo(
        json.dumps(
            {
                "current": current,
                "worker_service": "running" if worker.status().returncode == 0 else "stopped",
                "updater_service": "running" if updater.status().returncode == 0 else "stopped",
                "updater_plist_owned": updater.owns_installed_plist(),
                "drain_requested": update_drain_requested(paths.guard),
                "state": json.loads(state.model_dump_json()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@worker_app.command("uninstall-updater")
def worker_uninstall_updater(
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
    install_root: Annotated[
        Path | None,
        typer.Option(help="Managed versioned release root."),
    ] = None,
) -> None:
    """Unload and remove only the ads-booster-owned updater LaunchAgent."""
    _require_macos()
    paths = _managed_paths(_home(home), install_root)
    updater = _updater_launchd(
        paths,
        codex=Path("/nonexistent/codex"),
        uv=Path("/nonexistent/uv"),
        gh=Path("/nonexistent/gh"),
        interval_seconds=3600,
    )
    if updater.plist_path.exists() and not updater.owns_installed_plist():
        typer.echo("refusing to remove an updater plist not owned by ads-booster", err=True)
        raise typer.Exit(code=1)
    _ = updater.stop()
    if not updater.wait_until_stopped():
        typer.echo("updater service did not finish stopping", err=True)
        raise typer.Exit(code=1)
    if updater.plist_path.exists():
        updater.plist_path.unlink()
    typer.echo("updater service: uninstalled; worker state and credentials were preserved")


@worker_app.command("finish-bootstrap")
def worker_finish_bootstrap(  # noqa: C901, PLR0912 - one-time plist transaction.
    home: Annotated[Path, typer.Option(help="Agent state root.")],
    install_root: Annotated[Path, typer.Option(help="Managed versioned release root.")],
    uv: Annotated[Path, typer.Option(help="Pinned uv executable used for staging.")],
    gh: Annotated[
        Path | None,
        typer.Option(help="Pinned GitHub CLI executable used for artifact attestation."),
    ] = None,
    interval_seconds: Annotated[int, typer.Option(min=300, max=86400)] = 3600,
) -> None:
    """Finalize worker and updater services after verified install and enrollment."""
    _require_macos()
    paths = _managed_paths(_home(home), install_root)
    worker: MacWorkerLaunchd | None = None
    updater: MacWorkerUpdaterLaunchd | None = None
    worker_plist_backup: bytes | None = None
    updater_plist_backup: bytes | None = None
    try:
        release = current_installed_release(paths)
        if installed_version() != release.version:
            _update_failure("bootstrap command is not running from the current release")
        _ = MacWorkerStore(paths.agent_home).load()
        codex = resolve_codex_executable()
        if codex is None:
            _update_failure("official Codex CLI is unavailable for the launchd user")
        report = inspect_mac_worker(codex_executable=codex, resolve_codex=False)
        if not report.ready or report.version != release.version:
            _update_failure("candidate worker doctor is not ready at the exact version")
        worker = MacWorkerLaunchd(
            executable=paths.current / "bin" / "trace-marketing",
            codex_executable=codex,
            agent_home=paths.agent_home,
            plist_path=default_worker_plist_path(),
            install_root=paths.root,
        )
        updater = _updater_launchd(
            paths,
            codex=codex,
            uv=_resolve_uv(uv),
            gh=_resolve_gh(gh),
            interval_seconds=interval_seconds,
        )
        if worker.plist_path.exists():
            if not worker.owns_installed_plist():
                _update_failure("existing worker plist is not owned by ads-booster")
            worker_plist_backup = worker.plist_path.read_bytes()
        if updater.plist_path.exists():
            if not updater.owns_installed_plist():
                _update_failure("existing updater plist is not owned by ads-booster")
            updater_plist_backup = updater.plist_path.read_bytes()
        if worker.status().returncode == 0:
            _update_failure("operator must drain and stop the existing worker first")
        worker.install()
        verifier = WorkerRuntimeVerifier(paths=paths, command_runner=run_command)
        verifier.start_and_verify(worker, release)
        updater.install()
        if updater.start().returncode != 0 or updater.status().returncode != 0:
            _update_failure("updater LaunchAgent failed to start")
        UpdateStateStore(paths.state).save(
            UpdateState(
                status="healthy",
                current_version=release.version,
                last_known_good_version=release.version,
                candidate_version=release.version,
                commit_sha=release.receipt.commit_sha,
            )
        )
    except (CloudflareQueueError, OSError, ValueError, WorkerUpdateError) as error:
        if updater is not None:
            _ = updater.stop()
        if worker is not None:
            _ = worker.stop()
            _restore_plist(worker.plist_path, worker_plist_backup)
        if updater is not None:
            _restore_plist(updater.plist_path, updater_plist_backup)
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"managed worker bootstrapped: {release.version}")


@worker_app.command("set-state")
def worker_set_state(
    state: Annotated[str, typer.Option(help="active or draining")],
    url: Annotated[
        str | None,
        typer.Option(help="Cloudflare origin for remote administration."),
    ] = None,
    worker_id: Annotated[
        str | None,
        typer.Option(help="Worker ID from `worker list`; defaults to this Mac."),
    ] = None,
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    if state not in {"active", "draining"}:
        message = "state must be active or draining"
        raise typer.BadParameter(message)
    target_url, target_worker_id = _worker_admin_target(_home(home), url, worker_id)
    payload = _admin_post(
        target_url,
        f"/v1/workers/{target_worker_id}/state",
        {"state": state},
    )
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@worker_app.command("revoke")
def worker_revoke(
    url: Annotated[
        str | None,
        typer.Option(help="Cloudflare origin for remote administration."),
    ] = None,
    worker_id: Annotated[
        str | None,
        typer.Option(help="Worker ID from `worker list`; defaults to this Mac."),
    ] = None,
    home: Annotated[Path | None, typer.Option(help="Agent state root.")] = None,
) -> None:
    """Revoke this machine remotely; local files stay available for diagnosis."""
    target_url, target_worker_id = _worker_admin_target(_home(home), url, worker_id)
    payload = _admin_post(
        target_url,
        f"/v1/workers/{target_worker_id}/revoke",
        {},
    )
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@worker_app.command("list")
def worker_list(
    url: Annotated[str, typer.Option(help="Deployed Cloudflare workspace origin.")],
) -> None:
    token = _required("TRACE_MARKETING_CONTROL_TOKEN")
    with create_http_client() as http:
        response = http.get(
            f"{_https_origin(url)}/v1/workers",
            {"authorization": f"Bearer {token}"},
        )
    if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
        typer.echo(f"worker list failed with HTTP {response.status_code}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(response.json_object(), ensure_ascii=False, indent=2))


def _run_mac_worker(agent_home: Path, *, once: bool) -> None:
    try:
        config, credential = MacWorkerStore(agent_home).load()
    except CloudflareQueueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    heartbeat = DoctorHeartbeat(report=inspect_mac_worker(), checked_at=time.monotonic())
    root = agent_home / "marketing-worker" / "runtime"
    managed_paths = _managed_paths(agent_home, None)
    heartbeat_stop = Event()
    heartbeat_thread = Thread(
        target=_heartbeat_loop,
        args=(config, credential, heartbeat, heartbeat_stop, managed_paths),
        name="trace-marketing-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        with create_http_client(read_timeout=60.0) as http:
            broker = WorkerBrokerClient(http, config, credential, heartbeat)
            capture = build_hosted_capture_executor(agent_home, http)
            executable = resolve_codex_executable()
            if executable is None:
                reason = "codex_exec_unavailable"
                raise CloudflareQueueError(reason)
            executor = PlanlessHostedTaskExecutor(
                capture=capture,
                generation=HostedWorkspaceGenerationExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                judgment=HostedMarketingJudgmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                creative_judgment=HostedCreativeJudgmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                candidate_judgment=HostedCandidateJudgmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                experiment_evaluation=HostedExperimentEvaluationExecutor(),
                learning_judgment=HostedLearningJudgmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                reference_research=HostedReferenceResearchExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                outcome_reassessment=HostedOutcomeReassessmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                next_experiment=HostedNextExperimentJudgmentExecutor(
                    codex=CodexCli(executable=executable),
                    output_root=agent_home / "generated",
                ),
                feature_launch_run=HostedFeatureLaunchRunExecutor(
                    codex_executable=executable,
                    output_root=agent_home / "generated",
                ),
            )
            event_http = create_http_client()
            event_broker = WorkerBrokerClient(event_http, config, credential, heartbeat)
            worker: MarketingWorkerLoop[PlanlessPrepared] = MarketingWorkerLoop(
                broker=broker,
                inbox=MarketingInbox(root),
                preparer=executor,
                executor=executor,
                event_reporter=QueuedWorkerEventReporter(
                    event_broker,
                    on_stop=event_http.close,
                ),
            )
            try:
                recovered = worker.recover()
                if recovered:
                    typer.echo(f"recovered {recovered} interrupted task(s)")
                while True:
                    active = worker.tick(
                        accept_remote=not update_drain_requested(managed_paths.guard)
                    )
                    if once:
                        return
                    if not active:
                        time.sleep(config.poll_seconds)
            finally:
                worker.close()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)


def _heartbeat_loop(
    config: MacWorkerConfig,
    credential: MacWorkerCredential,
    heartbeat: DoctorHeartbeat,
    stop: Event,
    managed_paths: ManagedWorkerPaths,
) -> None:
    with create_http_client() as http:
        client = WorkerBrokerClient(http, config, credential, heartbeat)
        while not stop.is_set():
            with suppress(CloudflareQueueError):
                payload = client.heartbeat_once()
                version = payload.get("version")
                if isinstance(version, str):
                    _ = HeartbeatReceiptStore(managed_paths.heartbeat).save(
                        worker_id=config.worker_id,
                        version=version,
                    )
                target_version = payload.get("update_target_version")
                if isinstance(target_version, str):
                    _ = kickstart_managed_updater()
            _ = stop.wait(15)


def _managed_paths(agent_home: Path, install_root: Path | None) -> ManagedWorkerPaths:
    root = default_install_root() if install_root is None else install_root.expanduser()
    return ManagedWorkerPaths(root=root.absolute(), agent_home=agent_home.expanduser().absolute())


def _managed_worker_launchd(paths: ManagedWorkerPaths) -> MacWorkerLaunchd:
    return MacWorkerLaunchd(
        executable=paths.current / "bin" / "trace-marketing",
        agent_home=paths.agent_home,
        plist_path=default_worker_plist_path(),
        install_root=paths.root,
    )


def _updater_launchd(
    paths: ManagedWorkerPaths,
    *,
    codex: Path,
    uv: Path,
    gh: Path,
    interval_seconds: int,
) -> MacWorkerUpdaterLaunchd:
    return MacWorkerUpdaterLaunchd(
        executable=paths.current / "bin" / "trace-marketing",
        agent_home=paths.agent_home,
        install_root=paths.root,
        plist_path=default_updater_plist_path(),
        codex_executable=codex,
        uv_executable=uv,
        gh_executable=gh,
        interval_seconds=interval_seconds,
    )


def _resolve_uv(configured: Path | None) -> Path:
    resolved = str(configured.expanduser()) if configured is not None else shutil.which("uv")
    if resolved is None:
        _update_failure("uv is required for offline release staging")
    executable = Path(resolved).expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        _update_failure("configured uv executable is unavailable")
    return executable


def _resolve_gh(configured: Path | None) -> Path:
    resolved = str(configured.expanduser()) if configured is not None else shutil.which("gh")
    if resolved is None:
        _update_failure("GitHub CLI is required for release artifact attestation")
    executable = Path(resolved).expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        _update_failure("configured GitHub CLI executable is unavailable")
    return executable


def _mac_worker_updater(
    paths: ManagedWorkerPaths,
    *,
    http: HttpClient,
    uv: Path,
    gh: Path,
    drain_timeout_seconds: float,
) -> MacWorkerUpdater:
    service = _managed_worker_launchd(paths)
    verifier = WorkerRuntimeVerifier(paths=paths, command_runner=run_command)
    return MacWorkerUpdater(
        paths=paths,
        source=GitHubReleaseSource(
            http,
            GitHubArtifactAttestationVerifier(gh, run_command),
        ),
        installer=ManagedReleaseInstaller(paths, uv, run_command),
        service=service,
        verifier=verifier,
        state_store=UpdateStateStore(paths.state),
        drain_timeout_seconds=drain_timeout_seconds,
    )


def _require_macos() -> None:
    if sys.platform != "darwin":
        message = "Mac worker updater commands require macOS"
        raise typer.BadParameter(message)


def _update_failure(message: str) -> Never:
    raise WorkerUpdateError(message)


def _restore_plist(path: Path, previous: bytes | None) -> None:
    if previous is None:
        with suppress(FileNotFoundError):
            path.unlink()
        return
    temporary = path.with_suffix(".restore.tmp")
    _ = temporary.write_bytes(previous)
    temporary.chmod(0o600)
    _ = temporary.replace(path)


def _worker_launchd(agent_home: Path, *, require_codex: bool = False) -> MacWorkerLaunchd:
    _require_macos()
    paths = _managed_paths(agent_home, None)
    managed_executable = paths.current / "bin" / "trace-marketing"
    executable = (
        str(managed_executable) if managed_executable.is_file() else shutil.which("trace-marketing")
    )
    if executable is None:
        message = "trace-marketing is not installed on PATH"
        raise typer.BadParameter(message)
    codex_executable = resolve_codex_executable() if require_codex else None
    if require_codex and codex_executable is None:
        message = "codex is not installed on PATH; install Codex CLI and run `codex login`"
        raise typer.BadParameter(message)
    return MacWorkerLaunchd(
        executable=Path(executable),
        codex_executable=codex_executable,
        agent_home=agent_home,
        plist_path=default_worker_plist_path(),
        install_root=paths.root if managed_executable.is_file() else None,
    )


def _admin_post(url: str, path: str, payload: JsonObject) -> JsonObject:
    token = _required("TRACE_MARKETING_CONTROL_TOKEN")
    with create_http_client() as http:
        response = http.post_json(
            f"{_https_origin(url)}{path}",
            payload,
            {
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
        )
    if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
        typer.echo(
            f"Mac worker admin request failed with HTTP {response.status_code}",
            err=True,
        )
        raise typer.Exit(code=1)
    return response.json_object()


def _load_worker(agent_home: Path) -> tuple[MacWorkerConfig, MacWorkerCredential]:
    try:
        return MacWorkerStore(agent_home).load()
    except CloudflareQueueError as error:
        raise typer.BadParameter(str(error)) from error


def _worker_admin_target(
    agent_home: Path,
    url: str | None,
    worker_id: str | None,
) -> tuple[str, str]:
    if url is None and worker_id is None:
        config, _credential = _load_worker(agent_home)
        return config.control_plane_url, config.worker_id
    if url is None or worker_id is None:
        message = "url and worker-id must be provided together"
        raise typer.BadParameter(message)
    return _https_origin(url), worker_id


def _https_origin(value: str) -> str:
    try:
        return normalize_control_plane_origin(value)
    except ValueError as error:
        message = "url must be an HTTPS origin without a path"
        raise typer.BadParameter(message) from error


def _stop_worker_launchd(launchd: MacWorkerLaunchd) -> None:
    _ = launchd.stop()
    if not launchd.wait_until_stopped():
        typer.echo("worker service did not finish stopping; retry the command", err=True)
        raise typer.Exit(code=1)


class _ProcessResult(Protocol):
    """Minimal launchctl result surface consumed by CLI error presentation."""

    @property
    def returncode(self) -> int: ...

    @property
    def stderr(self) -> str: ...


def _process_error(prefix: str, result: _ProcessResult) -> str:
    return_code = result.returncode
    stderr = str(result.stderr or "").strip()
    return f"{prefix} ({return_code}){f': {stderr}' if stderr else ''}"


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
