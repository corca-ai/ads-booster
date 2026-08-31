from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ads_booster.providers.codex_cli import (
    CodexAppiumJobCallbacks,
    CodexAppiumReadyState,
    CodexAppiumSavedState,
    CodexCli,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue


def _write_saved_marker(
    workspace: Path,
    *,
    mode: int = 0o600,
) -> None:
    path = workspace / "codex-appium-saved.json"
    temporary = workspace / ".codex-appium-saved.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.fchmod(descriptor, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "trace.codex-appium-saved.v1",
                "session_id": "appium-1",
            },
            stream,
        )
    _ = temporary.replace(path)


def _write_ready_marker(workspace: Path) -> None:
    path = workspace / "codex-appium-ready.json"
    temporary = workspace / ".codex-appium-ready.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "trace.codex-appium-ready.v1",
                "session_id": "appium-1",
                "rendered_trace_item_titles": ["Focus block"],
            },
            stream,
        )
    _ = temporary.replace(path)


def _wait_for_marker(path: Path) -> None:
    deadline = time.monotonic() + 1
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert path.exists()


def test_codex_cli_runs_appium_job_with_trace_appium_permission_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, dict[str, JsonValue], JsonValue]] = []
    ready_states: list[CodexAppiumReadyState] = []
    saved_states: list[CodexAppiumSavedState] = []

    def run(command: list[str], **kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_marker(workspace)
        _wait_for_marker(workspace / "codex-appium-ready-verified.json")
        _write_saved_marker(workspace)
        collected_path = workspace / "codex-appium-collected.json"
        _wait_for_marker(collected_path)
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "session_id": "appium-1",
                    "session_closed": True,
                    "error_code": None,
                }
            ),
            encoding="utf-8",
        )
        schema_path = Path(command[command.index("--output-schema") + 1])
        calls.append((command, str(kwargs["input"]), kwargs, json.loads(schema_path.read_text())))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    workspace = tmp_path / "job"
    workspace.mkdir()

    result = CodexCli(tmp_path / "bin" / "codex").run_appium_job(
        "Operate Trace with Appium",
        {
            "type": "object",
            "properties": {"status": {"const": "completed"}},
            "required": ["status"],
            "additionalProperties": False,
        },
        workspace=workspace,
        timeout_seconds=240,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=lambda ready: ready_states.append(ready) is None,
            on_saved=lambda saved: saved_states.append(saved) is None,
        ),
    )

    command, prompt, options, schema = calls[0]
    assert result["status"] == "completed"
    assert ready_states == [
        CodexAppiumReadyState(
            schema="trace.codex-appium-ready.v1",
            session_id="appium-1",
            rendered_trace_item_titles=("Focus block",),
        )
    ]
    assert saved_states == [
        CodexAppiumSavedState(
            schema="trace.codex-appium-saved.v1",
            session_id="appium-1",
        )
    ]
    assert len(calls) == 1
    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    assert "--sandbox" not in command
    assert "danger-full-access" not in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    configs = tuple(command[index + 1] for index, value in enumerate(command) if value == "-c")
    assert configs == (
        "features.network_proxy=true",
        'default_permissions="trace-appium"',
        'permissions.trace-appium.extends=":workspace"',
        "permissions.trace-appium.network.enabled=true",
        'permissions.trace-appium.network.mode="full"',
        "permissions.trace-appium.network.allow_local_binding=false",
        'permissions.trace-appium.network.domains={"127.0.0.1"="allow",localhost="allow","::1"="allow"}',
    )
    assert command[command.index("--cd") + 1] == str(workspace)
    assert "--ephemeral" in command
    assert "--json" not in command
    assert prompt == "Operate Trace with Appium"
    assert options["timeout"] == 240
    assert "env" not in options
    assert isinstance(schema, dict)
    assert schema["required"] == ["status"]
    receipt = workspace / "codex-appium-invocation.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema": "trace.codex-appium-invocation.v1",
        "profile": "trace-appium",
        "invocation_count": 1,
    }
    acknowledgement = workspace / "codex-appium-collected.json"
    assert acknowledgement.stat().st_mode & 0o777 == 0o600
    assert json.loads(acknowledgement.read_text(encoding="utf-8")) == {
        "schema": "trace.codex-appium-collected.v1",
        "session_id": "appium-1",
        "collection_succeeded": True,
    }


def test_codex_cli_refuses_second_appium_invocation_from_same_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        _write_ready_marker(workspace)
        _wait_for_marker(workspace / "codex-appium-ready-verified.json")
        _write_saved_marker(workspace)
        collected_path = workspace / "codex-appium-collected.json"
        _wait_for_marker(collected_path)
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    workspace = tmp_path / "job"
    workspace.mkdir()
    codex = CodexCli(tmp_path / "bin" / "codex")
    schema: JsonObject = {
        "type": "object",
        "properties": {"status": {"const": "completed"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    _ = codex.run_appium_job(
        "Operate Trace with Appium",
        schema,
        workspace=workspace,
        timeout_seconds=240,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=lambda _ready: True,
            on_saved=lambda _saved: True,
        ),
    )

    with pytest.raises(RuntimeError, match="codex_appium_job_already_invoked"):
        _ = codex.run_appium_job(
            "Operate Trace with Appium",
            schema,
            workspace=workspace,
            timeout_seconds=240,
            callbacks=CodexAppiumJobCallbacks(
                on_ready=lambda _ready: True,
                on_saved=lambda _saved: True,
            ),
        )

    assert calls == 1
