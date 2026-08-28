from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from click import unstyle
from typer.testing import CliRunner

from ads_booster.cli.agent import app
from ads_booster.cli.marketing import app as marketing_app
from ads_booster.default_assets import default_iphone_ui_path

if TYPE_CHECKING:
    import pytest


def test_agent_help_keeps_existing_entrypoint_and_workspace_commands() -> None:
    # Given the installed agent command surface
    runner = CliRunner()

    # When the root help is rendered
    result = runner.invoke(app, ["--help"])
    output = unstyle(result.stdout)

    # Then the existing and additive commands remain discoverable
    assert result.exit_code == 0
    assert all(
        command in output for command in ("serve", "auth", "generate-one", "workspace", "service")
    )


def test_agent_serve_help_keeps_loopback_and_tunnel_options() -> None:
    # Given the additive workspace service command
    result = CliRunner().invoke(app, ["serve", "--help"])
    output = unstyle(result.stdout)

    # When the service help is rendered
    # Then its compatibility boundary exposes the supported transport controls
    assert result.exit_code == 0
    assert all(option in output for option in ("--host", "--port", "--tunnel"))
    assert "cloudflared" in output


def test_agent_auth_status_uses_a_clean_home_without_printing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an empty, isolated agent home
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path))

    # When the non-interactive auth status command runs
    result = CliRunner().invoke(app, ["auth", "status"])

    # Then it reports the state without attempting a login or exposing a token
    assert result.exit_code == 0
    assert "not logged in" in result.stdout
    assert "Bearer" not in result.stdout


def test_generate_one_help_exposes_only_live_wallpaper_inputs() -> None:
    # Given the context-driven one-shot command
    result = CliRunner().invoke(app, ["generate-one", "--help"])
    output = unstyle(result.stdout)

    # When its help is rendered
    # Then only the context, output, Appium, and timeout controls remain visible
    assert result.exit_code == 0
    assert "--context-file" in output
    assert "--output-root" in output
    assert "--appium-server" in output
    assert "--timeout-seconds" in output
    assert all(
        option not in output
        for option in ("--image-model", "--iphone-ui", "--state-root", "--capture-output-root")
    )


def test_default_iphone_ui_asset_is_available_to_the_installed_cli() -> None:
    assert default_iphone_ui_path().is_file()


def test_package_source_parses_on_the_declared_python_314_floor() -> None:
    package_root = Path(__file__).parents[2] / "src" / "ads_booster"

    for source in sorted(package_root.rglob("*.py")):
        _ = ast.parse(
            source.read_text(encoding="utf-8"),
            filename=str(source),
            feature_version=(3, 14),
        )


def test_marketing_worker_help_exposes_the_replaceable_mac_lifecycle() -> None:
    result = CliRunner().invoke(marketing_app, ["worker", "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert all(
        command in output
        for command in (
            "create-enrollment",
            "enroll",
            "doctor",
            "run",
            "install-service",
            "status",
            "update",
            "finish-bootstrap",
            "updater-status",
            "set-state",
            "revoke",
        )
    )


def test_worker_stop_treats_an_already_missing_launchd_service_as_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingLaunchdService:
        def stop(self) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["launchctl", "bootout"],
                returncode=3,
                stdout="",
                stderr="Boot-out failed: No such process",
            )

        def wait_until_stopped(self) -> bool:
            return True

    missing = MissingLaunchdService()

    def launchd_for(_home: Path) -> MissingLaunchdService:
        return missing

    monkeypatch.setattr("ads_booster.cli.marketing._worker_launchd", launchd_for)

    result = CliRunner().invoke(marketing_app, ["worker", "stop", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "worker service: stopped" in result.stdout
