from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ads_booster.providers.codex_cli import CodexCli

if TYPE_CHECKING:
    import pytest


def test_codex_cli_uses_ephemeral_read_only_structured_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, dict[str, object], object]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"answer":"ok"}', encoding="utf-8")
        schema_path = Path(command[command.index("--output-schema") + 1])
        calls.append((command, str(kwargs["input"]), kwargs, json.loads(schema_path.read_text())))
        return subprocess.CompletedProcess(command, 0, stdout='{"answer":"ok"}', stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    image = tmp_path / "reference.png"
    _ = image.write_bytes(b"png")

    result = CodexCli(tmp_path / "bin" / "codex").generate_json(
        "plan this task",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        images=(image,),
    )

    command, prompt, options, schema = calls[0]
    assert result == {"answer": "ok"}
    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--image") + 1] == str(image)
    assert command[-1] == "-"
    assert prompt == "plan this task"
    assert "env" not in options
    assert isinstance(schema, dict)
    assert schema["required"] == ["answer"]


def test_codex_cli_runs_appium_job_in_supplied_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, dict[str, object], object]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "session_id": "appium-1",
                    "app_group_export_seen": True,
                    "cleanup_completed": True,
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
    )

    command, prompt, options, schema = calls[0]
    assert result["status"] == "completed"
    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert command[command.index("--cd") + 1] == str(workspace)
    assert "--ephemeral" in command
    assert prompt == "Operate Trace with Appium"
    assert options["timeout"] == 240
    assert "env" not in options
    assert isinstance(schema, dict)
    assert schema["required"] == ["status"]
