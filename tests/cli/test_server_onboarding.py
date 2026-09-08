from __future__ import annotations

import getpass
import json
import shutil
import stat
import sys
import zoneinfo
from pathlib import Path
from typing import cast

import pytest
import typer
from typer.testing import CliRunner

from ads_booster.cli import server
from ads_booster.cli.marketing import app

pytestmark = pytest.mark.usefixtures("configured_paths")

SOURCE = Path(__file__).parents[2] / "docs/operations/agent-server"


@pytest.fixture
def configured_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(server, "ROOT", tmp_path / "install")
    (server.ROOT / "current").mkdir(parents=True)
    _ = (server.ROOT / "current/agent-manager.py").write_text("fixture")
    monkeypatch.setattr(server, "CONFIG", tmp_path / "config")
    monkeypatch.setattr(server, "UNITS", tmp_path / "units")
    monkeypatch.setattr(server, "resources", lambda: SOURCE)
    monkeypatch.setattr(server, "interactive_terminal", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")

    def executable(name: str) -> str:
        return f"/opt/tools/{name}"

    def execute(*_args: str) -> str:
        return "--token-file"

    monkeypatch.setattr(server, "executable", executable)
    monkeypatch.setattr(server, "execute", execute)
    return tmp_path


def setup_inputs(monkeypatch: pytest.MonkeyPatch, *, valid: bool = True) -> None:
    values = iter(
        [
            "https://agent.example.com",
            "approved-model",
            "marketing",
            "A123",
            "C123",
            "U123,U456",
            "U123",
        ]
    )

    def prompt(*_args: object, **_kwargs: object) -> str:
        return next(values)

    def confirm(*_args: object, **_kwargs: object) -> bool:
        return True

    secrets = iter(["xoxb-fixture-secret", "signing-fixture-secret", "tunnel-fixture-secret"])

    def secret(*_args: object) -> str:
        return next(secrets)

    def identity(*_args: object) -> dict[str, object]:
        return {"ok": valid, "team_id": "T123", "user_id": "UBOT", "bot_id": "B123"}

    monkeypatch.setattr(typer, "prompt", prompt)
    monkeypatch.setattr(typer, "confirm", confirm)
    monkeypatch.setattr(getpass, "getpass", secret)
    monkeypatch.setattr(server, "json_request", identity)


def test_setup_validates_identity_writes_private_config_and_only_own_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_inputs(monkeypatch)
    server.UNITS.mkdir()
    ear = server.UNITS / "cloudflared-ear.service"
    _ = ear.write_text("existing unrelated service")
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 0, result.output
    assert "fixture-secret" not in result.output
    assert ear.read_text() == "existing unrelated service"
    config = cast(
        "dict[str, object]", json.loads((server.CONFIG / "slack-installation.json").read_text())
    )
    assert config["team_id"] == "T123"
    assert config["members"] == [
        {"slack_user_id": "U123", "member_id": "U123", "can_approve": True},
        {"slack_user_id": "U456", "member_id": "U456", "can_approve": False},
    ]
    environment = (server.CONFIG / "agent.env").read_text()
    assert 'TRACE_MARKETING_SLACK_BOT_USER_ID="UBOT"' in environment
    assert 'TRACE_MARKETING_PUBLIC_ORIGIN="https://agent.example.com"' in environment
    for name in ["agent.env", "slack-installation.json", "tunnel.token"]:
        assert stat.S_IMODE((server.CONFIG / name).stat().st_mode) == 0o600
    tunnel = (server.UNITS / server.TUNNEL).read_text()
    assert "--token-file" in tunnel
    assert "tunnel-fixture-secret" not in tunnel
    assert "/opt/tools" in (server.UNITS / server.SERVICE).read_text()
    manifest = cast(
        "dict[str, object]", json.loads((server.CONFIG / "slack-app-manifest.json").read_text())
    )
    settings = cast("dict[str, object]", manifest["settings"])
    subscriptions = cast("dict[str, object]", settings["event_subscriptions"])
    assert subscriptions["request_url"] == "https://agent.example.com/channels/slack/events"


def test_bad_slack_auth_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_inputs(monkeypatch, valid=False)
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 1
    assert "slack_bot_auth_failed" in result.output
    assert not server.CONFIG.exists()
    assert not server.UNITS.exists()


def test_setup_preserves_existing_unit_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_inputs(monkeypatch)
    server.UNITS.mkdir()
    unit = server.UNITS / server.SERVICE
    _ = unit.write_text("operator-owned")
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 1
    assert unit.read_text() == "operator-owned"
    assert not server.CONFIG.exists()
    server.CONFIG.mkdir()
    _ = (server.CONFIG / "agent.env").write_text("original-secret")
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 1
    assert (server.CONFIG / "agent.env").read_text() == "original-secret"
    assert "original-secret" not in result.output


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        'https://example.com"injected',
        "https://bad..example.com",
        "https://example.com\nEVIL=1",
    ],
)
def test_invalid_origin_cannot_enter_environment(origin: str) -> None:
    with pytest.raises(RuntimeError):
        _ = server.origin_value(origin)


def test_manifest_generation_before_setup_needs_no_secrets() -> None:
    result = CliRunner().invoke(
        app, ["server", "manifest", "--origin", "https://agent.example.com", "--bootstrap"]
    )
    assert result.exit_code == 0
    value = cast("dict[str, object]", json.loads(result.output))
    assert "event_subscriptions" not in cast("dict[str, object]", value["settings"])
    assert not server.CONFIG.exists()


def test_start_reports_linger_requirement_and_uses_only_owned_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.private_write(
        server.CONFIG / "server.json", '{"origin":"https://agent.example.com","tunnel":true}'
    )
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> str:
        calls.append(args)
        return "no" if args[0] == "loginctl" else ""

    monkeypatch.setattr(server, "execute", run)
    result = CliRunner().invoke(app, ["server", "start"])
    assert result.exit_code == 0
    assert "enable-linger" in result.output
    assert (
        "systemctl",
        "--user",
        "enable",
        "--now",
        server.SERVICE,
        server.TIMER,
        server.TUNNEL,
    ) in calls
    assert not any("cloudflared-ear.service" in args for args in calls)


def test_private_write_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _ = target.write_text("preserve")
    link = tmp_path / "secret"
    link.symlink_to(target)
    with pytest.raises(RuntimeError):
        server.private_write(link, "replace")
    assert target.read_text() == "preserve"


def test_noninteractive_setup_refuses_before_reading_or_printing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "interactive_terminal", lambda: False)
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 1
    assert "setup_requires_interactive_terminal" in result.output
    assert not server.CONFIG.exists()


def test_interrupted_setup_resumes_without_reentering_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_inputs(monkeypatch)
    write = server.private_write

    def interrupted(path: Path, text: str) -> None:
        if path.name == "agent.env":
            raise OSError
        write(path, text)

    monkeypatch.setattr(server, "private_write", interrupted)
    failed = CliRunner().invoke(app, ["server", "setup"])
    assert failed.exit_code == 1
    assert (server.CONFIG / "setup-pending.json").is_file()
    monkeypatch.setattr(server, "private_write", write)
    resumed = CliRunner().invoke(app, ["server", "setup"])
    assert resumed.exit_code == 0, resumed.output
    assert not (server.CONFIG / "setup-pending.json").exists()
    assert (server.CONFIG / "agent.env").is_file()
    assert "fixture-secret" not in resumed.output
    assert CliRunner().invoke(app, ["server", "setup"]).exit_code == 0


def test_resume_preserves_operator_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_inputs(monkeypatch)
    write = server.private_write

    def interrupted(path: Path, text: str) -> None:
        if path.name == "agent.env":
            raise OSError
        write(path, text)

    monkeypatch.setattr(server, "private_write", interrupted)
    assert CliRunner().invoke(app, ["server", "setup"]).exit_code == 1
    write(server.CONFIG / "slack-installation.json", "operator edit")
    monkeypatch.setattr(server, "private_write", write)
    result = CliRunner().invoke(app, ["server", "setup"])
    assert result.exit_code == 1
    assert "setup_resume_preserves_operator_edit" in result.output
    assert (server.CONFIG / "slack-installation.json").read_text() == "operator edit"


def test_doctor_reports_missing_setup_as_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(_name: str) -> str:
        return "/usr/bin/fixture"

    monkeypatch.setattr(shutil, "which", which)
    result = CliRunner().invoke(app, ["server", "doctor"])
    assert result.exit_code == 1
    value = cast("dict[str, object]", json.loads(result.output))
    assert value["ready"] is False
    assert "gh" not in value
    assert "cloudflared" not in value


def test_setup_creates_private_knowledge_root_before_actor_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    setup_inputs(monkeypatch)
    knowledge_root = server.ROOT / "knowledge"
    assert not knowledge_root.exists()

    # When
    server.setup_config()

    # Then
    assert stat.S_IMODE(knowledge_root.stat().st_mode) == 0o700
    assert (knowledge_root / "index.sqlite").is_file()
    assert (server.CONFIG / "knowledge-control/identity.json").is_file()
    assert (server.CONFIG / "knowledge-policy.json").is_file()


def test_setup_initializes_knowledge_without_system_timezone_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_inputs(monkeypatch)
    original_path = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(())
    zoneinfo.ZoneInfo.clear_cache()
    try:
        result = CliRunner().invoke(app, ["server", "setup"])

        assert result.exit_code == 0, result.output
        assert (server.ROOT / "knowledge").is_dir()
        assert not (server.CONFIG / "setup-pending.json").exists()
    finally:
        zoneinfo.reset_tzpath(original_path)
        zoneinfo.ZoneInfo.clear_cache()
