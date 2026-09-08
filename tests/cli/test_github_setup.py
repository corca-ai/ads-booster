from __future__ import annotations

import getpass
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ads_booster.cli import server
from ads_booster.tools.github_issues import GitHubIssues

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def read_token(_prompt: str) -> str:
    return "fixture_token"


def access_ok(_self: GitHubIssues) -> None:
    pass


def test_github_setup_preserves_slack_and_saves_private_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "CONFIG", tmp_path)
    _ = (tmp_path / "agent.env").write_text("existing-settings")
    monkeypatch.setattr(getpass, "getpass", read_token)
    monkeypatch.setattr(GitHubIssues, "check_access", access_ok)
    result = CliRunner().invoke(server.app, ["github-setup"])
    assert result.exit_code == 0, result.output
    assert "fixture_token" not in result.output
    assert (tmp_path / "github.token").read_text() == "fixture_token\n"
    assert (tmp_path / "github.token").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "agent.env").read_text() == "existing-settings"


def test_rejected_credential_does_not_replace_existing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "CONFIG", tmp_path)
    _ = (tmp_path / "github.token").write_text("previous")
    monkeypatch.setattr(getpass, "getpass", read_token)

    def reject(_self: GitHubIssues) -> None:
        message = "github_http_401"
        raise ValueError(message)

    monkeypatch.setattr(GitHubIssues, "check_access", reject)
    result = CliRunner().invoke(server.app, ["github-setup"])
    assert result.exit_code == 1
    assert "fixture_token" not in result.output
    assert "Traceback" not in result.output
    assert (tmp_path / "github.token").read_text() == "previous"
