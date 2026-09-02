from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Thread
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from ads_booster.cli import marketing as marketing_cli
from ads_booster.marketing.models import (
    MarketingTask,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from ads_booster.marketing.worker_broker import (
    MacWorkerConfig,
    MacWorkerCredential,
    MacWorkerStore,
    WorkerBrokerClient,
    enroll_mac_worker,
    normalize_control_plane_origin,
)
from ads_booster.marketing.worker_doctor import MacWorkerDoctorReport, inspect_mac_worker
from ads_booster.marketing.worker_launchd import (
    MacWorkerLaunchd,
    MacWorkerUpdaterLaunchd,
    kickstart_managed_updater,
)
from ads_booster.marketing.worker_update import HeartbeatReceiptStore, ManagedWorkerPaths
from ads_booster.transport.http import HttpResponse
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _task() -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="hosted:task-1",
        payload={"pipeline": "hosted_workspace_capture_v1"},
        created_at=datetime.now(UTC),
    )


@dataclass
class StubHttp:
    responses: list[HttpResponse]
    requests: list[tuple[str, JsonObject, Mapping[str, str]]] = field(default_factory=list)

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        _ = (url, headers)
        message = "unexpected GET"
        raise AssertionError(message)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        self.requests.append((url, payload, headers))
        return self.responses.pop(0)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected form request"
        raise AssertionError(message)


def _response(payload: JsonObject, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode(), {})


def test_enrollment_writes_machine_credential_separately_from_portable_config(
    tmp_path: Path,
) -> None:
    http = StubHttp(
        [
            _response(
                {
                    "worker_id": "worker-1",
                    "worker_token": "worker-secret",
                    "display_name": "Studio Mac",
                    "pool": "appium",
                    "state": "active",
                },
                status=201,
            )
        ]
    )

    config, credential = enroll_mac_worker(
        http,
        control_plane_url="https://workspace.example.test/",
        enrollment_code="one-time-code",
        heartbeat={
            "version": "0.2.3",
            "capabilities": {"native_appium": True},
            "doctor": {"ready": True, "summary": "ready"},
        },
    )
    store = MacWorkerStore(tmp_path)
    store.save(config, credential)

    assert store.load() == (config, credential)
    assert config.control_plane_url == "https://workspace.example.test"
    assert "worker-secret" not in store.config_path.read_text()
    assert "worker-secret" in store.credential_path.read_text()
    assert stat.S_IMODE(store.config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.credential_path.stat().st_mode) == 0o600
    assert http.requests[0][0] == "https://workspace.example.test/v1/workers/enroll"
    assert "authorization" not in http.requests[0][2]


@pytest.mark.parametrize(
    "url",
    [
        "http://workspace.example.test",
        "https://user@workspace.example.test",
        "https://workspace.example.test/path",
        "https://workspace.example.test?token=secret",
    ],
)
def test_control_plane_origin_rejects_non_origin_urls(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS origin"):
        _ = normalize_control_plane_origin(url)


def test_broker_claim_ack_and_callback_use_only_the_worker_scoped_token() -> None:
    task = _task()
    callback = TaskCallback(
        callback_id="task-1:completed",
        task_id=task.task_id,
        run_id=task.run_id,
        account_id=task.account_id,
        kind=task.kind,
        result=TaskResult(status=TaskStatus.FAILED, failure_code="fixture_failure"),
        completed_at=datetime.now(UTC),
    )
    http = StubHttp(
        [
            _response(
                {
                    "leases": [
                        {
                            "message_id": "task-1",
                            "lease_id": "lease-1",
                            "attempts": 1,
                            "task": _JSON_OBJECT.validate_json(task.model_dump_json()),
                        }
                    ]
                }
            ),
            _response({"accepted": 1, "retried": 0}),
            _response({"accepted": True, "duplicate": False}),
            _response({"accepted": True}, status=202),
        ]
    )
    client = WorkerBrokerClient(
        http=http,
        config=MacWorkerConfig(
            worker_id="worker-1",
            display_name="Studio Mac",
            control_plane_url="https://workspace.example.test",
        ),
        credential=MacWorkerCredential(
            worker_token="worker-secret"  # noqa: S106 - inert fixture credential.
        ),
        heartbeat=lambda: {
            "version": "0.2.3",
            "capabilities": {"native_appium": True},
            "doctor": {"ready": True, "summary": "ready"},
        },
    )

    leases = client.pull()
    client.acknowledge(ack_lease_ids=(leases[0].lease_id,))
    client.mark_execution_started(task.task_id)
    client.deliver(callback)

    assert leases[0].task == task
    assert [request[0] for request in http.requests] == [
        "https://workspace.example.test/v1/workers/tasks/claim",
        "https://workspace.example.test/v1/workers/tasks/ack",
        "https://workspace.example.test/v1/workers/tasks/executing",
        "https://workspace.example.test/v1/workers/task-callbacks",
    ]
    assert all(request[2]["authorization"] == "Bearer worker-secret" for request in http.requests)
    assert all("queue" not in json.dumps(request).lower() for request in http.requests)


def test_heartbeat_returns_the_server_update_target() -> None:
    client = WorkerBrokerClient(
        http=StubHttp(
            [
                _response(
                    {
                        "worker_id": "worker-1",
                        "state": "active",
                        "seen_at": "2026-09-02T00:00:00Z",
                        "update_target_version": "0.4.14",
                    }
                )
            ]
        ),
        config=MacWorkerConfig(
            worker_id="worker-1",
            display_name="Studio Mac",
            control_plane_url="https://workspace.example.test",
        ),
        credential=MacWorkerCredential(
            worker_token="worker-secret"  # noqa: S106 - inert fixture credential.
        ),
        heartbeat=lambda: {
            "version": "0.4.13",
            "capabilities": {"native_appium": True},
            "doctor": {"ready": True, "summary": "ready"},
        },
    )

    receipt = client.heartbeat_once()

    assert receipt["version"] == "0.4.13"
    assert receipt["update_target_version"] == "0.4.14"


def test_launchd_lifecycle_resolution_does_not_require_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(marketing_cli.sys, "platform", "darwin")
    executable = tmp_path / "trace-marketing"
    monkeypatch.setattr(marketing_cli.shutil, "which", lambda _name: str(executable))

    def unexpected_codex_lookup() -> Path | None:
        message = "Codex lookup must not run for lifecycle-only commands"
        raise AssertionError(message)

    monkeypatch.setattr(marketing_cli, "resolve_codex_executable", unexpected_codex_lookup)

    launchd = marketing_cli._worker_launchd(tmp_path / "agent-home")

    assert launchd.codex_executable is None


def test_worker_launchagent_install_requires_a_codex_executable(tmp_path: Path) -> None:
    launchd = MacWorkerLaunchd(
        executable=tmp_path / "bin" / "trace-marketing",
        agent_home=tmp_path / "agent-home",
        plist_path=tmp_path / "worker.plist",
    )

    with pytest.raises(ValueError, match="Codex executable is required"):
        launchd.install()


def test_worker_launchagent_contains_no_credential_or_person_specific_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_CODEX_MODEL", "gpt-oss-20b")
    monkeypatch.setenv("TRACE_AGENT_DEVICE_UDID", "A" * 36)
    monkeypatch.setenv("TRACE_MARKETING_CONTROL_TOKEN", "must-not-be-persisted")
    plist_path = tmp_path / "com.corca.trace-marketing-worker.plist"
    launchd = MacWorkerLaunchd(
        executable=tmp_path / "bin" / "trace-marketing",
        codex_executable=tmp_path / "bin" / "codex",
        agent_home=tmp_path / "agent-home",
        plist_path=plist_path,
    )

    launchd.install()

    payload = cast(
        "dict[str, object]",
        plistlib.loads(plist_path.read_bytes()),
    )
    arguments = payload["ProgramArguments"]
    environment = payload["EnvironmentVariables"]
    assert isinstance(arguments, list)
    assert arguments[-2:] == ["worker", "service"]
    assert isinstance(environment, dict)
    assert environment["TRACE_AGENT_HOME"] == str((tmp_path / "agent-home").resolve())
    assert environment["TRACE_CODEX_BIN"] == str((tmp_path / "bin" / "codex").resolve())
    assert launchd.installed_codex_executable() == (tmp_path / "bin" / "codex").resolve()
    assert environment["TRACE_CODEX_MODEL"] == "gpt-oss-20b"
    assert environment["TRACE_AGENT_DEVICE_UDID"] == "A" * 36
    assert "usr/bin" in environment["PATH"]
    assert "token" not in plist_path.read_text().lower()
    assert "keychain" not in plist_path.read_text().lower()
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


def test_managed_launchagents_keep_the_current_symlink_and_separate_updater(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_MARKETING_CONTROL_TOKEN", "must-not-be-persisted")
    root = tmp_path / "managed"
    release = root / "releases" / "1.2.3"
    executable = release / "bin" / "trace-marketing"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o700)
    current = root / "current"
    current.symlink_to(release, target_is_directory=True)
    codex = tmp_path / "bin" / "codex"
    uv = tmp_path / "bin" / "uv"
    gh = tmp_path / "bin" / "gh"
    codex.parent.mkdir(parents=True)
    codex.touch()
    uv.touch()
    gh.touch()
    worker_plist = tmp_path / "worker.plist"
    updater_plist = tmp_path / "updater.plist"
    worker = MacWorkerLaunchd(
        executable=current / "bin" / "trace-marketing",
        codex_executable=codex,
        agent_home=tmp_path / "agent",
        plist_path=worker_plist,
        install_root=root,
    )
    updater = MacWorkerUpdaterLaunchd(
        executable=current / "bin" / "trace-marketing",
        codex_executable=codex,
        uv_executable=uv,
        gh_executable=gh,
        agent_home=tmp_path / "agent",
        install_root=root,
        plist_path=updater_plist,
        interval_seconds=600,
    )

    worker.install()
    updater.install()

    worker_payload = cast("dict[str, object]", plistlib.loads(worker_plist.read_bytes()))
    updater_payload = cast("dict[str, object]", plistlib.loads(updater_plist.read_bytes()))
    worker_arguments = cast("list[str]", worker_payload["ProgramArguments"])
    updater_arguments = cast("list[str]", updater_payload["ProgramArguments"])
    assert worker_arguments[0] == str(current / "bin" / "trace-marketing")
    assert updater_arguments[:3] == [
        str(current / "bin" / "trace-marketing"),
        "worker",
        "update",
    ]
    assert updater_arguments[updater_arguments.index("--gh") + 1] == str(gh.resolve())
    assert updater_payload["Label"] == "com.corca.trace-marketing-updater"
    assert updater_payload["RunAtLoad"] is True
    assert updater_payload["StartInterval"] == 600
    assert "KeepAlive" not in updater_payload
    assert worker.owns_installed_plist()
    assert updater.owns_installed_plist()
    assert "must-not-be-persisted" not in updater_plist.read_text(encoding="utf-8")


def test_updater_kickstart_does_not_force_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    result = kickstart_managed_updater()

    assert result.returncode == 0
    assert commands == [
        ("/bin/launchctl", "kickstart", f"gui/{os.getuid()}/com.corca.trace-marketing-updater")
    ]


def test_heartbeat_target_kickstarts_the_loaded_updater(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stop = Event()
    signals: list[None] = []
    report = MacWorkerDoctorReport(ready=True, summary="ready", checks={}, version="0.4.13")
    heartbeat = marketing_cli.DoctorHeartbeat(report=report, checked_at=0.0)
    config = MacWorkerConfig(
        worker_id="worker-1",
        display_name="Studio Mac",
        control_plane_url="https://workspace.example.test",
    )
    credential = MacWorkerCredential(worker_token="worker-secret")  # noqa: S106
    paths = ManagedWorkerPaths(root=tmp_path / "managed", agent_home=tmp_path / "agent")
    monkeypatch.setattr(marketing_cli, "create_http_client", lambda: nullcontext(StubHttp([])))

    def heartbeat_once(_client: WorkerBrokerClient) -> JsonObject:
        return {"version": "0.4.13", "update_target_version": "0.4.14"}

    monkeypatch.setattr(
        WorkerBrokerClient,
        "heartbeat_once",
        heartbeat_once,
    )

    def kickstart() -> subprocess.CompletedProcess[str]:
        signals.append(None)
        stop.set()
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(marketing_cli, "kickstart_managed_updater", kickstart)

    marketing_cli._heartbeat_loop(config, credential, heartbeat, stop, paths)

    assert signals == [None]
    receipt = HeartbeatReceiptStore(paths.heartbeat).load()
    assert receipt is not None
    assert receipt.version == "0.4.13"


def test_doctor_heartbeat_serves_cached_state_during_a_slow_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = MacWorkerDoctorReport(ready=False, summary="cached", checks={}, version="0.2.3")
    refreshed = MacWorkerDoctorReport(ready=True, summary="ready", checks={}, version="0.2.3")
    refresh_started = Event()
    release_refresh = Event()
    refreshed_results: list[JsonObject] = []
    cached_results: list[JsonObject] = []
    cached_returned = Event()

    def slow_inspection() -> MacWorkerDoctorReport:
        refresh_started.set()
        assert release_refresh.wait(timeout=1)
        return refreshed

    monkeypatch.setattr(marketing_cli, "inspect_mac_worker", slow_inspection)
    heartbeat = marketing_cli.DoctorHeartbeat(report=cached, checked_at=0.0, refresh_seconds=0.0)
    refresher = Thread(target=lambda: refreshed_results.append(heartbeat()))
    refresher.start()
    assert refresh_started.wait(timeout=1)

    def read_cached() -> None:
        cached_results.append(heartbeat())
        cached_returned.set()

    cached_reader = Thread(target=read_cached)
    cached_reader.start()
    returned_during_refresh = cached_returned.wait(timeout=1)
    release_refresh.set()
    refresher.join(timeout=1)
    cached_reader.join(timeout=1)

    assert returned_during_refresh
    assert cached_results[0]["doctor"] == {"ready": False, "summary": "cached"}
    assert refreshed_results[0]["doctor"] == {"ready": True, "summary": "ready"}


def test_worker_admin_http_failure_is_a_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = StubHttp([_response({}, status=503)])
    monkeypatch.setenv("TRACE_MARKETING_CONTROL_TOKEN", "admin-secret")
    monkeypatch.setattr(marketing_cli, "create_http_client", lambda: nullcontext(http))

    result = CliRunner().invoke(
        marketing_cli.app,
        [
            "worker",
            "set-state",
            "--state",
            "draining",
            "--url",
            "https://workspace.example.test",
            "--worker-id",
            "worker-1",
        ],
    )

    assert result.exit_code == 1
    assert "Mac worker admin request failed with HTTP 503" in result.stderr
    assert "Usage:" not in result.stderr


@pytest.mark.parametrize("case", ["missing-plist", "moved", "installed"])
def test_worker_status_checks_the_launchagent_pinned_codex(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pinned = None if case == "missing-plist" else tmp_path / case / "codex"
    if case == "installed" and pinned is not None:
        pinned.parent.mkdir()
        pinned.touch()
        pinned.chmod(0o700)
    captured: list[Path | None] = []

    class StatusLaunchd:
        plist_path = tmp_path / "worker.plist"

        @staticmethod
        def status() -> object:
            return type("Status", (), {"returncode": 1})()

        @staticmethod
        def installed_codex_executable() -> Path | None:
            return pinned

    def inspect_pinned(
        *,
        codex_executable: Path | None = None,
        resolve_codex: bool = True,
    ) -> MacWorkerDoctorReport:
        assert resolve_codex is False
        captured.append(codex_executable)
        available = codex_executable is not None and codex_executable.is_file()
        return MacWorkerDoctorReport(
            ready=available,
            summary="ready" if available else "missing: codex_cli",
            checks={"codex_cli": available},
            version="0.2.3",
        )

    monkeypatch.setattr(marketing_cli, "_worker_launchd", lambda _home: StatusLaunchd())
    monkeypatch.setattr(marketing_cli, "inspect_mac_worker", inspect_pinned)
    monkeypatch.setattr(
        marketing_cli,
        "resolve_codex_executable",
        lambda: (_ for _ in ()).throw(AssertionError("ambient Codex lookup is forbidden")),
    )

    result = CliRunner().invoke(
        marketing_cli.app,
        ["worker", "status", "--home", str(tmp_path / "agent")],
    )

    assert result.exit_code == 0
    assert captured == [pinned]
    payload = json.loads(result.stdout)
    assert payload["codex_runtime"]["executable"] == (str(pinned) if pinned else None)
    assert payload["doctor"]["ready"] is (case == "installed")


def test_doctor_boots_the_selected_simulator_before_checking_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []
    codex = tmp_path / "bin" / "codex"
    codex.parent.mkdir()
    codex.touch()
    codex.chmod(0o700)

    def which(command: str) -> str | None:
        return f"/usr/local/bin/{command}" if command in {"xcrun", "appium"} else None

    def run(command: tuple[str, ...]) -> str | None:
        commands.append(command)
        if command[1:5] == ("simctl", "list", "devices", "available"):
            return json.dumps(
                {
                    "devices": {
                        "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                            {
                                "name": "iPhone 17 Pro",
                                "udid": "simulator-1",
                                "state": "Shutdown",
                                "isAvailable": True,
                            }
                        ]
                    }
                }
            )
        if command[1:4] == ("simctl", "listapps", "simulator-1"):
            return "com.corca.Trace"
        if command[1:4] == ("driver", "list", "--installed"):
            return '{"xcuitest": {}}'
        return ""

    monkeypatch.setattr("ads_booster.marketing.worker_doctor.platform.system", lambda: "Darwin")
    monkeypatch.setattr("ads_booster.marketing.worker_doctor.shutil.which", which)
    monkeypatch.setattr(
        "ads_booster.marketing.worker_doctor.resolve_codex_executable",
        lambda: codex,
    )
    monkeypatch.setattr("ads_booster.marketing.worker_doctor._run", run)

    report = inspect_mac_worker()

    assert report.ready is True
    assert report.checks["codex_cli"] is True
    assert report.checks["codex_authenticated"] is True
    assert ("/usr/local/bin/xcrun", "simctl", "boot", "simulator-1") in commands
    assert (
        "/usr/local/bin/xcrun",
        "simctl",
        "bootstatus",
        "simulator-1",
        "-b",
    ) in commands


def test_the_worker_advertises_the_job_kinds_it_can_actually_run() -> None:
    """The control plane leases by this, so what it says has to be what this build does.

    A worker that says nothing is read as capture-only, which is exactly what a Mac enrolled
    before caption generation existed can do. During the minutes between deploying the Worker
    and that Mac updating itself, that default is what keeps a caption batch away from it.
    """
    # Given one worker's heartbeat
    report = MacWorkerDoctorReport(ready=True, summary="ready", checks={}, version="0.3.12")

    # When the control plane reads its capabilities
    capabilities = report.heartbeat()["capabilities"]

    # Then the advertisement is a scalar the control plane will not flatten to null, and it
    # names both jobs this build routes.
    assert isinstance(capabilities, dict)
    assert capabilities["task_kinds"] == "capture,generate_candidates"
    assert capabilities["native_appium"] is True
    assert capabilities["feedback_context_v1"] is True
    assert all(isinstance(value, str | bool) for value in capabilities.values())
