from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from trace_capture.cli.agent import app

if TYPE_CHECKING:
    from pathlib import Path

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


def test_generate_one_help_keeps_context_and_image_model_contract() -> None:
    # Given the context-driven one-shot command
    result = CliRunner().invoke(app, ["generate-one", "--help"])

    # When its help is rendered
    # Then the required context input and compatible image model remain visible
    assert result.exit_code == 0
    assert "--context-file" in result.stdout
    assert "--image-model" in result.stdout
    assert "gpt-5.6-luna" in result.stdout
