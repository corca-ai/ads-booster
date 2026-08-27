from __future__ import annotations

import json
import plistlib
import stat
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
from ads_booster.marketing.worker_launchd import MacWorkerLaunchd
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
    client.deliver(callback)

    assert leases[0].task == task
    assert [request[0] for request in http.requests] == [
        "https://workspace.example.test/v1/workers/tasks/claim",
        "https://workspace.example.test/v1/workers/tasks/ack",
        "https://workspace.example.test/v1/workers/task-callbacks",
    ]
    assert all(request[2]["authorization"] == "Bearer worker-secret" for request in http.requests)
    assert all("queue" not in json.dumps(request).lower() for request in http.requests)


def test_worker_launchagent_contains_no_credential_or_person_specific_path(tmp_path: Path) -> None:
    plist_path = tmp_path / "com.corca.trace-marketing-worker.plist"
    launchd = MacWorkerLaunchd(
        executable=tmp_path / "bin" / "trace-marketing",
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
    assert "usr/bin" in environment["PATH"]
    assert "token" not in plist_path.read_text().lower()
    assert "keychain" not in plist_path.read_text().lower()
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


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


def test_doctor_boots_the_selected_simulator_before_checking_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

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
    monkeypatch.setattr("ads_booster.marketing.worker_doctor._run", run)

    report = inspect_mac_worker()

    assert report.ready is True
    assert ("/usr/local/bin/xcrun", "simctl", "boot", "simulator-1") in commands
    assert (
        "/usr/local/bin/xcrun",
        "simctl",
        "bootstatus",
        "simulator-1",
        "-b",
    ) in commands
