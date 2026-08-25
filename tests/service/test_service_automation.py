from __future__ import annotations

import stat
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from typing import TYPE_CHECKING, NoReturn, final

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from trace_capture.automation import (
    AutomationQueue,
    CampaignCreate,
    CampaignStore,
    QueueRecord,
    QueueState,
)
from trace_capture.cli.agent import app
from trace_capture.contracts import TraceRunResult
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.contracts.run import TraceRunState
from trace_capture.providers.errors import ProviderError
from trace_capture.runtime.generate_one import GenerateOneOptions
from trace_capture.service.cli import bootstrap_launchd_service
from trace_capture.service.launchd import LaunchdConfig, install_plist
from trace_capture.service.runtime import create_service_app
from trace_capture.service.state import ServiceState, ServiceStateStore, ensure_workspace
from trace_capture.service.worker import ProductionGenerateOneRunner, ServiceWorkerConfig
from trace_capture.workspace import MemberId, SqliteWorkspaceStore, WorkspaceId

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from trace_capture.providers.image_generation import ImageGenerationRequest


_NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
_FIXTURE_PROVIDER_FAILED = "fixture_provider_failed"
_FIXTURE_PROVIDER_MESSAGE = "fixture provider failure"


def _bundle(request_id: str) -> MarketingContextBundle:
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


@final
class _LifecycleFixtureRunner:
    output_root: Path

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
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


@final
class _FailingImageGenerator:
    def generate(self, request: ImageGenerationRequest) -> NoReturn:
        del request
        raise ProviderError(_FIXTURE_PROVIDER_FAILED, _FIXTURE_PROVIDER_MESSAGE)


def test_generation_route_feeds_the_persistent_worker_to_review(tmp_path: Path) -> None:
    # Given a running service with a fixture generation runner
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    runner = _LifecycleFixtureRunner(tmp_path / "generated")
    completed = Event()
    client = TestClient(
        create_service_app(
            tmp_path,
            runner,
            worker_config=ServiceWorkerConfig(
                poll_interval_seconds=0.01,
                on_completed=lambda _record: completed.set(),
            ),
        ),
        base_url="https://testserver",
    )

    # When an authenticated member submits one typed generation context
    with client:
        login = client.post(
            "/api/auth/login",
            json={
                "workspace_id": workspace.workspace.workspace_id,
                "member_id": member.member.member_id,
                "workspace_code": workspace.access_code,
                "member_code": member.invite_code,
            },
        )
        submitted = client.post(
            "/api/generation",
            json={"bundle": _bundle("automatic").model_dump(mode="json")},
        )

        # Then the persistent worker consumes the generated queue record
        assert login.status_code == 200
        assert submitted.status_code == 201
        queued = QueueRecord.model_validate_json(submitted.content)
        assert completed.wait(timeout=5)

    reviewed = AutomationQueue(tmp_path).get(queued.workspace_id, queued.queue_id)
    assert reviewed.state is QueueState.REVIEW
    assert reviewed.artifact_path == "outputs/final.png"


def test_service_worker_when_finite_campaign_is_active_then_it_generates_every_variation(
    tmp_path: Path,
) -> None:
    # Given a two-variation campaign persisted before the service starts
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    bundle = _bundle("campaign-template")
    campaign = CampaignStore(tmp_path).create(
        CampaignCreate(
            workspace_id=workspace.workspace.workspace_id,
            name="Two variations",
            persona=bundle.persona,
            promotion_material=bundle.promotion_material,
            reference_date=bundle.reference_date,
            device=bundle.device,
            variation_count=2,
        )
    )
    runner = _LifecycleFixtureRunner(tmp_path / "generated")
    completed = Event()
    completion_count = 0

    def record_completion(_record: QueueRecord) -> None:
        nonlocal completion_count
        completion_count += 1
        if completion_count == 2:
            completed.set()

    client = TestClient(
        create_service_app(
            tmp_path,
            runner,
            worker_config=ServiceWorkerConfig(
                poll_interval_seconds=0.01,
                on_completed=record_completion,
            ),
        ),
        base_url="https://testserver",
    )

    # When the persistent service worker runs
    with client:
        assert completed.wait(timeout=5)

    # Then it emits exactly two unique variations and retains both for review
    records = AutomationQueue(tmp_path).list_workspace(campaign.workspace_id)
    assert tuple(record.bundle.variation_index for record in records) == (1, 0)
    assert all(record.state is QueueState.REVIEW for record in records)
    assert completion_count == 2
    assert member.member.workspace_id == campaign.workspace_id


def test_production_runner_when_provider_fails_then_it_returns_a_failed_result(
    tmp_path: Path,
) -> None:
    # Given production generation options and an image provider failure
    system_ui = tmp_path / "system-ui.png"
    _ = system_ui.write_bytes(b"fixture-system-ui")
    runner = ProductionGenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            state_root=tmp_path / "state",
            capture_output_root=tmp_path / "capture",
            iphone_ui_path=system_ui,
            reference_root=tmp_path,
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
            image_model="fixture-image-model",
        ),
        image_generator=_FailingImageGenerator(),
    )

    # When the service runs one generation attempt
    result = runner.run(_bundle("provider-failure"))

    # Then the failure is returned to the durable worker instead of terminating the service task
    assert result.state is TraceRunState.FAILED
    assert result.run_id == "provider-failure"
    assert result.idempotency_key == "provider-failure-v1"


def test_service_state_round_trips_the_live_public_url(tmp_path: Path) -> None:
    # Given a service state with a live tunnel URL
    state_store = ServiceStateStore(tmp_path)
    state = ServiceState(
        workspace_id=WorkspaceId("workspace-1"),
        member_id=MemberId("member-1"),
        tunnel="cloudflared",
        public_url="https://trace-team.trycloudflare.com",
    )

    # When the service state is persisted and loaded
    state_store.save(state)

    # Then the public URL remains available to service status consumers
    assert state_store.load() == state


def test_service_install_defaults_to_cloudflared_workspace_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an isolated agent home and a local trace-agent executable
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path / "agent-home"))

    def fake_which(name: str) -> str | None:
        return str(tmp_path / "bin" / name) if name == "trace-agent" else None

    monkeypatch.setattr("trace_capture.service.cli.shutil.which", fake_which)
    plist_path = tmp_path / "com.corca.trace-agent.plist"

    # When the service is installed without specifying a tunnel
    result = CliRunner().invoke(
        app,
        [
            "service",
            "install",
            "--no-load",
            "--workspace-name",
            "Launch team",
            "--plist",
            str(plist_path),
        ],
    )

    # Then the persistent workspace service requests the public tunnel by default
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Workspace code:" not in result.stdout
    assert "Member code:" not in result.stdout
    assert "trace-agent workspace access" in result.stdout
    assert "<string>cloudflared</string>" in plist_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    state = ServiceStateStore(tmp_path / "agent-home").load()
    assert state is not None
    assert SqliteWorkspaceStore(tmp_path / "agent-home").get_workspace(state.workspace_id).name == (
        "Launch team"
    )


def test_launchd_bootstrap_retries_transient_teardown_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given launchd is still completing the previous job's teardown
    attempts = 0

    def fake_run(
        _args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return subprocess.CompletedProcess(
                _args,
                5,
                stdout="",
                stderr="Bootstrap failed: 5: Input/output error",
            )
        return subprocess.CompletedProcess(_args, 0, stdout="", stderr="")

    def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("trace_capture.service.cli.subprocess.run", fake_run)
    monkeypatch.setattr("trace_capture.service.cli.time.sleep", no_wait)

    # When the workspace service is bootstrapped
    result = bootstrap_launchd_service("gui/501", tmp_path / "service.plist")

    # Then only the transient teardown error is retried
    assert result.returncode == 0
    assert attempts == 3


def test_service_install_requires_a_name_for_a_fresh_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a fresh service state without a workspace name
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path / "agent-home"))
    plist_path = tmp_path / "com.corca.trace-agent.plist"

    # When the operator omits the first workspace name
    result = CliRunner().invoke(
        app,
        ["service", "install", "--no-load", "--plist", str(plist_path)],
    )

    # Then setup stops with a precise name requirement before creating the plist
    assert result.exit_code == 2
    assert "workspace name" in (result.stdout + result.stderr).lower()
    assert not plist_path.exists()


def test_service_install_workspace_name_updates_existing_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an existing workspace with the legacy default name
    home = tmp_path / "agent-home"
    monkeypatch.setenv("TRACE_AGENT_HOME", str(home))
    store = SqliteWorkspaceStore(home)
    state_store = ServiceStateStore(home)
    state = ensure_workspace(store, state_store, workspace_name="Trace team")

    def fake_which(name: str) -> str | None:
        return str(tmp_path / "bin" / name) if name == "trace-agent" else None

    monkeypatch.setattr(
        "trace_capture.service.cli.shutil.which",
        fake_which,
    )
    plist_path = tmp_path / "com.corca.trace-agent.plist"

    # When the operator reruns setup with a custom workspace name
    result = CliRunner().invoke(
        app,
        [
            "service",
            "install",
            "--no-load",
            "--workspace-name",
            "Launch archive",
            "--plist",
            str(plist_path),
        ],
    )

    # Then the stored workspace name is updated without changing its identity
    assert result.exit_code == 0
    assert SqliteWorkspaceStore(home).get_workspace(state.state.workspace_id).name == (
        "Launch archive"
    )


def test_workspace_access_command_prints_one_composite_login_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a bootstrapped workspace whose codes are not printed during setup
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path))
    store = SqliteWorkspaceStore(tmp_path)
    state_store = ServiceStateStore(tmp_path)
    provisioned = store.create_workspace("Trace team")
    member = store.create_member(provisioned.workspace.workspace_id, "Owner")
    state_store.save(
        ServiceState(
            workspace_id=provisioned.workspace.workspace_id,
            member_id=member.member.member_id,
        )
    )

    # When the operator explicitly asks for workspace access details
    result = CliRunner().invoke(app, ["workspace", "access"])

    # Then the command rotates and prints one copyable value containing the four browser values
    assert result.exit_code == 0
    access_lines = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("Workspace access ID (shown once; not written to logs): ")
    ]
    assert len(access_lines) == 1
    access_id = access_lines[0].removeprefix(
        "Workspace access ID (shown once; not written to logs): "
    )
    access_parts = access_id.split("%")
    assert len(access_parts) == 4
    assert access_parts[:2] == [
        str(provisioned.workspace.workspace_id),
        str(member.member.member_id),
    ]
    assert all(access_parts[2:])


def test_launchd_plist_passes_the_absolute_cloudflared_path(tmp_path: Path) -> None:
    # Given a launchd service configured with the Homebrew cloudflared binary
    cloudflared_path = tmp_path / "bin" / "cloudflared"
    cloudflared_path.parent.mkdir()
    _ = cloudflared_path.write_bytes(b"binary")
    cloudflared_path.chmod(0o700)
    plist_path = tmp_path / "com.corca.trace-agent.plist"

    # When the launchd plist is written
    install_plist(
        LaunchdConfig(
            executable=tmp_path / "bin" / "trace-agent",
            agent_home=tmp_path / "agent-home",
            host="127.0.0.1",
            port=8765,
            tunnel="cloudflared",
            cloudflared_path=cloudflared_path,
        ),
        plist_path,
    )

    # Then launchd receives the absolute binary path instead of relying on its restricted PATH
    serialized = plist_path.read_text(encoding="utf-8")
    assert "TRACE_AGENT_CLOUDFLARED" in serialized
    assert str(cloudflared_path) in serialized
