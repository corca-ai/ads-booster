from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from trace_capture.cli.agent import app
from trace_capture.cli.marketing import app as marketing_app
from trace_capture.default_assets import default_iphone_ui_path

if TYPE_CHECKING:
    import pytest


def test_agent_help_keeps_existing_entrypoint_and_workspace_commands() -> None:
    # Given the installed agent command surface
    runner = CliRunner()

    # When the root help is rendered
    result = runner.invoke(app, ["--help"])

    # Then the existing and additive commands remain discoverable
    assert result.exit_code == 0
    assert all(
        command in result.stdout
        for command in ("serve", "auth", "generate-one", "workspace", "service")
    )


def test_agent_serve_help_keeps_loopback_and_tunnel_options() -> None:
    # Given the additive workspace service command
    result = CliRunner().invoke(app, ["serve", "--help"])

    # When the service help is rendered
    # Then its compatibility boundary exposes the supported transport controls
    assert result.exit_code == 0
    assert all(option in result.stdout for option in ("--host", "--port", "--tunnel"))
    assert "cloudflared" in result.stdout


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


def test_generate_one_help_keeps_context_and_iphone_ui_without_image_model_contract() -> None:
    # Given the context-driven one-shot command
    result = CliRunner().invoke(app, ["generate-one", "--help"])

    # When its help is rendered
    # Then the context and system UI inputs remain visible without Image Model options
    assert result.exit_code == 0
    assert "--context-file" in result.stdout
    assert "--image-model" not in result.stdout
    assert "--iphone-ui" in result.stdout


def test_default_iphone_ui_asset_is_available_to_the_installed_cli() -> None:
    assert default_iphone_ui_path().is_file()


def test_package_source_parses_on_the_declared_python_313_floor() -> None:
    package_root = Path(__file__).parents[2] / "src" / "trace_capture"

    for source in sorted(package_root.rglob("*.py")):
        _ = ast.parse(
            source.read_text(encoding="utf-8"),
            filename=str(source),
            feature_version=(3, 13),
        )


def test_marketing_worker_help_exposes_the_replaceable_mac_lifecycle() -> None:
    result = CliRunner().invoke(marketing_app, ["worker", "--help"])

    assert result.exit_code == 0
    assert all(
        command in result.stdout
        for command in (
            "create-enrollment",
            "enroll",
            "doctor",
            "run",
            "install-service",
            "status",
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

    monkeypatch.setattr("trace_capture.cli.marketing._worker_launchd", launchd_for)

    result = CliRunner().invoke(marketing_app, ["worker", "stop", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "worker service: stopped" in result.stdout
