from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ads_booster.tools.github_issues import token_from_env

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.mark.parametrize("key", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_environment_auth_without_dedicated_file(key: str) -> None:
    assert token_from_env({key: "fixture_env"}) == "fixture_env"


def test_explicit_disable_and_credential_precedence(tmp_path: Path) -> None:
    path = tmp_path / ".config/trace-marketing/github.token"
    path.parent.mkdir(parents=True)
    _ = path.write_text("fixture_file")
    path.chmod(0o600)
    env = {"GH_TOKEN": "fixture_primary", "GITHUB_TOKEN": "fixture_secondary"}
    assert token_from_env(env) == "fixture_file"
    assert token_from_env({**env, "TRACE_MARKETING_GITHUB_ENABLED": "false"}) is None
    assert (
        token_from_env(
            {
                "GH_TOKEN": "fixture_primary",
                "GITHUB_TOKEN": "fixture_secondary",
                "TRACE_MARKETING_GITHUB_TOKEN_FILE": str(path),
            }
        )
        == "fixture_file"
    )
    path.unlink()
    assert token_from_env(env) == "fixture_primary"
    with pytest.raises(ValueError, match="private_regular_file"):
        _ = token_from_env({**env, "TRACE_MARKETING_GITHUB_TOKEN_FILE": str(path)})
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="private_regular_file"):
        _ = token_from_env(env)


@pytest.mark.parametrize("key", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_malformed_environment_token_is_not_silently_replaced(key: str) -> None:
    with pytest.raises(ValueError, match=r"^github_token_invalid$"):
        _ = token_from_env({key: "secret invalid", "GITHUB_TOKEN": "secret invalid"})


@pytest.mark.parametrize("outcome", ["ok", "missing", "rejected", "timeout", "invalid"])
def test_cli_login_is_fixed_host_noninteractive_and_secret_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    binary = tmp_path / "gh"
    _ = binary.write_text("fixture")
    binary.chmod(0o700)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv == [str(binary), "auth", "token", "--hostname", "github.com"]
        assert kwargs["env"] == {
            "PATH": str(tmp_path),
            "GH_HOST": "elsewhere.example",
            "GH_PROMPT_DISABLED": "1",
        }
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 10
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 10, output="private_secret")
        return subprocess.CompletedProcess(
            argv,
            1 if outcome == "rejected" else 0,
            "private secret" if outcome == "invalid" else "fixture_cli\n",
            "private_secret",
        )

    monkeypatch.setattr(subprocess, "run", run)
    if outcome == "missing":
        binary.unlink()
    env = {"PATH": str(tmp_path), "GH_HOST": "elsewhere.example"}
    if outcome == "invalid":
        with pytest.raises(ValueError, match=r"^github_token_invalid$"):
            _ = token_from_env(env)
    else:
        assert token_from_env(env) == ("fixture_cli" if outcome == "ok" else None)
    assert len(calls) == (0 if outcome == "missing" else 1)


def test_missing_path_does_not_execute_current_directory_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    binary = tmp_path / "gh"
    _ = binary.write_text("#!/bin/sh\nprintf 'fixture_cwd\\n'\n")
    binary.chmod(0o700)
    assert token_from_env({}) is None
    assert token_from_env({"PATH": ""}) is None
