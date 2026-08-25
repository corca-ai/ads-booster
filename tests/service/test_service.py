from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING, Self, final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from trace_capture.automation import AutomationQueue, QueueState, QueueSubmission
from trace_capture.cli.agent import app
from trace_capture.contracts import TraceRunResult
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.contracts.run import TraceRunState
from trace_capture.service.launchd import LaunchdConfig, install_plist
from trace_capture.service.readiness import wait_for_service_ready
from trace_capture.service.runtime import TunnelName, create_service_app, prepare_service
from trace_capture.service.state import (
    ServiceState,
    ServiceStateError,
    ServiceStateStore,
    ensure_workspace,
)
from trace_capture.service.worker import ServiceWorkerConfig
from trace_capture.tunnel.cloudflared import CloudflaredTunnel
from trace_capture.web.app import create_app
from trace_capture.workspace import MemberId, SqliteWorkspaceStore, WorkspaceId

if TYPE_CHECKING:
    from pathlib import Path


_NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


def _service_bundle(request_id: str) -> MarketingContextBundle:
    return MarketingContextBundle.model_validate(
        {
            "schema_version": "trace.marketing-context.v1",
            "request_id": request_id,
            "persona": {
                "persona_id": "student",
                "country": "JP",
                "locale": "ja-JP",
                "age_group": "18-24",
                "occupation": "student",
                "traits": ["focused"],
                "interests": ["study"],
            },
            "promotion_material": {
                "promotion_material_id": "exam",
                "feature": "countdown",
                "concept": "exam preparation",
                "tone": ["calm"],
            },
            "reference_date": _NOW.isoformat(),
            "device": {
                "kind": "simulator",
                "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
                "platform_version": "26.5",
                "device_name": "iPhone 17 Pro",
            },
        }
    )


def _service_submission(request_id: str) -> QueueSubmission:
    return QueueSubmission(
        workspace_id=WorkspaceId("workspace-1"),
        idempotency_key=f"enqueue-{request_id}",
        bundle=_service_bundle(request_id),
        due_at=_NOW - timedelta(seconds=1),
    )


@final
class _LifecycleFixtureRunner:
    output_root: Path
    calls: int

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.calls = 0

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        self.calls += 1
        job_root = self.output_root / bundle.request_id
        component = job_root / "work" / "trace-components.png"
        output = job_root / "outputs" / "final.png"
        component.parent.mkdir(parents=True, exist_ok=True)
        _ = component.write_bytes(b"fixture-component")
        output.parent.mkdir(parents=True, exist_ok=True)
        _ = output.write_bytes(b"fixture-output")
        return TraceRunResult(
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v1",
            input_digest="1" * 64,
            state=TraceRunState.COMPLETED,
            component_artifact="work/trace-components.png",
            component_artifact_sha256=sha256(b"fixture-component").hexdigest(),
            output_image="outputs/final.png",
            output_image_sha256=sha256(b"fixture-output").hexdigest(),
        )


def test_service_lifecycle_processes_due_queue_work_and_stops_cleanly(tmp_path: Path) -> None:
    # Given a durable due record and a fixture runner behind the real queue worker ports
    queue = AutomationQueue(tmp_path)
    queued = queue.enqueue(_service_submission("service-request"))
    runner = _LifecycleFixtureRunner(tmp_path / "generated")
    recorded = Event()
    client = TestClient(
        create_service_app(
            tmp_path,
            runner,
            worker_config=ServiceWorkerConfig(
                poll_interval_seconds=0.01,
                on_completed=lambda _record: recorded.set(),
            ),
        ),
        base_url="https://testserver",
    )

    # When the service lifespan starts and then shuts down
    with client:
        assert recorded.wait(timeout=5)
        reviewed = queue.get(queued.workspace_id, queued.queue_id)

    # Then the scheduler claimed the record, the worker linked verified artifacts to review,
    # and shutdown cancelled the worker before a second due record could be processed.
    assert (reviewed.state, reviewed.run_id, reviewed.run_idempotency_key) == (
        QueueState.REVIEW,
        "service-request",
        "service-request-v1",
    )
    assert reviewed.artifact_path == "outputs/final.png"
    assert reviewed.artifact_sha256 == sha256(b"fixture-output").hexdigest()
    second = queue.enqueue(_service_submission("after-shutdown"))
    assert runner.calls == 1
    assert queue.get(second.workspace_id, second.queue_id).state is QueueState.SUBMITTED


def test_static_workspace_is_served_by_fastapi(tmp_path: Path) -> None:
    # Given
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32))

    # When
    workspace = client.get("/")
    script = client.get("/static/workspace-live.js")

    # Then
    assert workspace.status_code == 200
    assert "Trace 워크스페이스" in workspace.text
    assert 'lang="ko"' in workspace.text
    assert "후보 자동 생성" in workspace.text
    assert "캡션·주제 승인" in workspace.text
    assert script.status_code == 200
    assert "fetch(" in script.text


def test_workspace_bootstrap_persists_only_ids_and_displays_codes_once(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    state_store = ServiceStateStore(tmp_path)

    # When
    first = ensure_workspace(store, state_store, workspace_name="Launch team")
    second = ensure_workspace(store, state_store)

    # Then
    assert first.workspace_code is not None
    assert first.member_code is not None
    assert second.workspace_code is None
    assert second.member_code is None
    assert store.get_workspace(first.state.workspace_id).name == "Launch team"
    state_bytes = state_store.path.read_bytes()
    assert first.workspace_code.encode() not in state_bytes
    assert first.member_code.encode() not in state_bytes
    assert stat.S_IMODE(state_store.path.stat().st_mode) == 0o600


def test_cli_workspace_show_and_rotation_revoke_old_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path))
    provisioned = ensure_workspace(
        SqliteWorkspaceStore(tmp_path),
        ServiceStateStore(tmp_path),
        workspace_name="Launch team",
    )
    old_workspace_code = provisioned.workspace_code
    old_member_code = provisioned.member_code

    # When
    shown = CliRunner().invoke(app, ["workspace", "show"])
    rotated = CliRunner().invoke(app, ["workspace", "rotate-code"])

    # Then
    assert old_workspace_code is not None
    assert old_member_code is not None
    assert shown.exit_code == 0
    assert provisioned.state.workspace_id in shown.stdout
    assert old_workspace_code not in shown.stdout
    assert old_member_code not in shown.stdout
    assert rotated.exit_code == 0
    assert "shown once" in rotated.stdout
    store = SqliteWorkspaceStore(tmp_path)
    assert not store.verify_workspace_code(provisioned.state.workspace_id, old_workspace_code)
    assert not store.verify_member_code(
        provisioned.state.workspace_id, provisioned.state.member_id, old_member_code
    )


def test_cli_workspace_add_member_issues_a_hashed_one_time_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path))
    provisioned = ensure_workspace(
        SqliteWorkspaceStore(tmp_path),
        ServiceStateStore(tmp_path),
        workspace_name="Launch team",
    )

    result = CliRunner().invoke(app, ["workspace", "add-member", "--name", "Grace"])

    assert result.exit_code == 0
    lines = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line)
    member_id = MemberId(lines["Member ID"])
    invite_code = lines["Member code"]
    assert lines["Display name"] == "Grace"
    store = SqliteWorkspaceStore(tmp_path)
    assert store.verify_member_code(provisioned.state.workspace_id, member_id, invite_code)
    assert invite_code.encode() not in store.database_path.read_bytes()


def test_launchd_plist_uses_loopback_and_contains_no_codes(tmp_path: Path) -> None:
    # Given
    agent_home = tmp_path / "agent-home"
    plist_path = tmp_path / "com.corca.trace-agent.plist"
    config = LaunchdConfig(
        executable=tmp_path / "bin" / "trace-agent",
        agent_home=agent_home,
        host="127.0.0.1",
        port=8765,
        tunnel="none",
    )

    # When
    install_plist(config, plist_path)
    serialized = plist_path.read_text(encoding="utf-8")

    # Then
    assert "<key>RunAtLoad</key>" in serialized
    assert "<key>KeepAlive</key>" in serialized
    assert "<string>127.0.0.1</string>" in serialized
    assert str(agent_home) in serialized
    assert "workspace_code" not in serialized
    assert "member_code" not in serialized
    assert stat.S_IMODE(agent_home.stat().st_mode) == 0o700


def test_missing_cloudflared_returns_truthful_local_fallback(tmp_path: Path) -> None:
    # Given
    tunnel = CloudflaredTunnel(binary=None, log_path=tmp_path / "tunnel.log")

    # When
    result = tunnel.start("http://127.0.0.1:8765")

    # Then
    assert result.public_url is None
    assert result.process is None
    assert result.detail == "cloudflared is not installed; local access only"


def test_service_readiness_keeps_emitted_url_when_local_dns_cannot_probe_tunnel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a locally healthy service and a URL already emitted by cloudflared
    state = ServiceState(
        workspace_id=WorkspaceId("workspace-1"),
        member_id=MemberId("member-1"),
        tunnel="cloudflared",
        public_url="https://tunnel.trycloudflare.com",
    )
    ServiceStateStore(tmp_path).save(state)

    class _LocalHealthClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, _headers: dict[str, str]) -> SimpleNamespace:
            assert url == "http://127.0.0.1:8765/health"
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        "trace_capture.service.readiness.create_http_client",
        _LocalHealthClient,
    )
    monkeypatch.setattr("trace_capture.service.readiness._READY_TIMEOUT_SECONDS", 0.01)

    # When local DNS cannot resolve the quick-tunnel hostname
    local_url, public_url = wait_for_service_ready(tmp_path, TunnelName.CLOUDFLARED)

    # Then readiness still exposes the URL emitted by the running tunnel
    assert local_url == "http://127.0.0.1:8765"
    assert public_url == "https://tunnel.trycloudflare.com"


def test_service_status_reports_emitted_url_when_public_dns_probe_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a healthy local service with a URL emitted by cloudflared
    home = tmp_path / "agent-home"
    monkeypatch.setenv("TRACE_AGENT_HOME", str(home))
    ServiceStateStore(home).save(
        ServiceState(
            workspace_id=WorkspaceId("workspace-1"),
            member_id=MemberId("member-1"),
            tunnel="cloudflared",
            public_url="https://tunnel.trycloudflare.com",
        )
    )
    plist_path = tmp_path / "com.corca.trace-agent.plist"
    plist_path.touch()
    monkeypatch.setattr("trace_capture.service.cli.default_plist_path", lambda: plist_path)

    class _LocalHealthClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, _headers: dict[str, str]) -> SimpleNamespace:
            assert url == "http://127.0.0.1:8765/health"
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        "trace_capture.service.cli.create_http_client",
        _LocalHealthClient,
    )

    # When status is requested from a host that cannot resolve the public hostname
    result = CliRunner().invoke(app, ["service", "status"])

    # Then the emitted team URL is not replaced by the loopback fallback
    assert result.exit_code == 0
    assert "public URL: https://tunnel.trycloudflare.com" in result.stdout
    assert "public URL: unavailable" not in result.stdout


def test_malformed_agent_home_fails_without_leaking_the_bound_port(tmp_path: Path) -> None:
    # Given
    malformed_home = tmp_path / "agent-home"
    _ = malformed_home.write_text("not a directory", encoding="utf-8")

    # When / Then
    with pytest.raises(ServiceStateError):
        _ = prepare_service(
            malformed_home,
            host="127.0.0.1",
            port=0,
            tunnel_name=TunnelName.NONE,
        )


def test_new_cli_surfaces_preserve_existing_commands() -> None:
    # Given / When
    result = CliRunner().invoke(app, ["--help"])
    serve = CliRunner().invoke(app, ["serve", "--help"])

    # Then
    assert result.exit_code == 0
    commands = ("auth", "generate-one", "workspace", "service")
    assert all(command in result.stdout for command in commands)
    assert serve.exit_code == 0
    assert "--host" in serve.stdout
    assert "--port" in serve.stdout
