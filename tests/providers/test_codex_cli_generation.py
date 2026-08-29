from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

if TYPE_CHECKING:
    import pytest

    from ads_booster.transport.json_types import JsonValue


def test_codex_cli_runs_generation_job_without_appium_profile_or_handshake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, dict[str, JsonValue]]] = []
    schemas: list[JsonObject] = []

    def run(command: list[str], **kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        _ = output_path.write_text('{"candidates":[]}', encoding="utf-8")
        schemas.append(_JSON_OBJECT.validate_json(schema_path.read_text(encoding="utf-8")))
        calls.append((command, str(kwargs["input"]), kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    workspace = tmp_path / "generation"
    workspace.mkdir()

    result = CodexCli(tmp_path / "bin" / "codex").run_generation_job(
        "Generate marketing drafts",
        {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"persona_domain": {"type": ["string", "null"]}},
                    },
                }
            },
            "required": ["candidates"],
            "additionalProperties": False,
        },
        workspace=workspace,
        timeout_seconds=120,
    )

    command, prompt, options = calls[0]
    assert result == {"candidates": []}
    assert len(calls) == 1
    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    assert "--output-schema" in command
    assert "--output-last-message" in command
    assert "--cd" in command
    assert command[command.index("--cd") + 1] == str(workspace)
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--sandbox" not in command
    assert "-c" not in command
    assert "trace-appium" not in command
    assert prompt == "Generate marketing drafts"
    assert options["timeout"] == 120
    properties = schemas[0]["properties"]
    assert isinstance(properties, dict)
    candidates = properties["candidates"]
    assert isinstance(candidates, dict)
    candidate_schema = candidates["items"]
    assert isinstance(candidate_schema, dict)
    assert candidate_schema["required"] == ["persona_domain"]
    assert "env" not in options
    assert not (workspace / "codex-appium-ready.json").exists()
    assert not (workspace / "codex-appium-saved.json").exists()
    receipt = workspace / "codex-generation-invocation.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema": "trace.codex-generation-invocation.v1",
        "invocation_count": 1,
    }
