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
        output_path.write_text('{"answer":"ok"}', encoding="utf-8")
        schema_path = Path(command[command.index("--output-schema") + 1])
        calls.append((command, str(kwargs["input"]), kwargs, json.loads(schema_path.read_text())))
        return subprocess.CompletedProcess(command, 0, stdout='{"answer":"ok"}', stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")

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
