from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ads_booster.providers.codex_cli import CodexCli

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue


def test_codex_cli_runs_image_edit_with_source_image_without_appium_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, dict[str, JsonValue]]] = []

    def run(command: list[str], **kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        calls.append((command, str(kwargs["input"]), kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    workspace = tmp_path / "image-edit"
    workspace.mkdir()
    image = workspace / "trace-native.png"
    _ = image.write_bytes(b"native-png")
    schema: JsonObject = {
        "type": "object",
        "properties": {"status": {"const": "completed"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    result = CodexCli(tmp_path / "bin" / "codex").run_image_edit_job(
        "Add the iOS lock-screen UI while preserving the Trace wallpaper.",
        schema,
        image=image,
        workspace=workspace,
        timeout_seconds=300,
    )

    command, prompt, options = calls[0]
    assert result == {"status": "completed"}
    assert len(calls) == 1
    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    assert command[command.index("--image") + 1] == str(image.resolve())
    assert command.index("--image") < command.index("--output-schema")
    assert command[command.index("--cd") + 1] == str(workspace)
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--skip-git-repo-check" in command
    assert "-c" not in command
    assert "trace-appium" not in command
    assert prompt == "Add the iOS lock-screen UI while preserving the Trace wallpaper."
    assert options["timeout"] == 300
    assert "env" not in options
    assert not (workspace / "codex-appium-invocation.json").exists()
    assert not (workspace / "codex-generation-invocation.json").exists()
    receipt = workspace / "codex-imagegen-invocation.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema": "trace.codex-imagegen-invocation.v1",
        "invocation_count": 1,
    }


def test_codex_cli_refuses_second_image_edit_invocation_from_same_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    workspace = tmp_path / "image-edit"
    workspace.mkdir()
    image = workspace / "trace-native.png"
    _ = image.write_bytes(b"native-png")
    schema: JsonObject = {
        "type": "object",
        "properties": {"status": {"const": "completed"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    codex = CodexCli(tmp_path / "bin" / "codex")

    _ = codex.run_image_edit_job(
        "Add the iOS lock-screen UI.",
        schema,
        image=image,
        workspace=workspace,
        timeout_seconds=300,
    )

    with pytest.raises(RuntimeError, match="codex_image_edit_job_already_invoked"):
        _ = codex.run_image_edit_job(
            "Add the iOS lock-screen UI.",
            schema,
            image=image,
            workspace=workspace,
            timeout_seconds=300,
        )

    assert calls == 1
