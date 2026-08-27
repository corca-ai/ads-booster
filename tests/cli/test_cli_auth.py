from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ads_booster.auth.models import OAuthCredential
from ads_booster.auth.store import AuthStore
from ads_booster.cli.agent import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_cli_auth_status_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AuthStore(tmp_path / "auth.json")
    monkeypatch.setattr(AuthStore, "default", lambda: store)

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "OpenAI ChatGPT/Codex OAuth: not logged in" in result.stdout


def test_cli_auth_status_with_openai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(
        OAuthCredential(
            provider="openai-codex",
            access_token="openai.fake",
            refresh_token="openai.ref",
            expires_at=10000.0,
            account_id="openai_acc",
        )
    )
    monkeypatch.setattr(AuthStore, "default", lambda: store)

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "OpenAI ChatGPT/Codex OAuth: logged in" in result.stdout
    assert "openai_acc" in result.stdout


def test_cli_auth_logout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(
        OAuthCredential(
            provider="openai-codex",
            access_token="openai.fake",
            refresh_token="openai.ref",
            expires_at=10000.0,
        )
    )
    monkeypatch.setattr(AuthStore, "default", lambda: store)

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert "OpenAI ChatGPT/Codex OAuth credentials cleared" in result.stdout
    assert store.load() is None
