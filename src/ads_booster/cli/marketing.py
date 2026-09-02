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

from ads_booster.marketing.errors import CloudflareQueueError
from ads_booster.marketing.hosted_generation import (
    HostedWorkspaceGenerationExecutor,
    PlanlessHostedTaskExecutor,
)
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
    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonObject

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300

app = typer.Typer(no_args_is_help=True, help="Operate the dynamic marketing account loop.")
worker_app = typer.Typer(no_args_is_help=True, help="Enroll and operate a replaceable Mac worker.")
app.add_typer(worker_app, name="worker")


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
            )
            worker = MarketingWorkerLoop(
                broker=broker,
                inbox=MarketingInbox(root),
                preparer=executor,
                executor=executor,
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
