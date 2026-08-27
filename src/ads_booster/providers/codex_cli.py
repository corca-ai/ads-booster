from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CodexCliError(RuntimeError):
    """A sanitized failure at the official Codex CLI process boundary."""


@dataclass(frozen=True, slots=True)
class CodexCli:
    executable: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    model: str | None = None

    def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        images: tuple[Path, ...] = (),
    ) -> JsonObject:
        """Run one ephemeral Codex turn and return its schema-constrained JSON output."""
        with tempfile.TemporaryDirectory(prefix="trace-codex-") as directory:
            root = Path(directory)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            _ = schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            command = [str(self.executable), "exec"]
            if images:
                command.extend(("--image", *(str(path) for path in images)))
            command.extend(
                (
                    "--ephemeral",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "--cd",
                    str(root),
                )
            )
            if self.model is not None:
                command.extend(("--model", self.model))
            command.append("-")
            try:
                completed = subprocess.run(  # noqa: S603
                    command,
                    input=prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except OSError as error:
                message = "codex_cli_unavailable"
                raise CodexCliError(message) from error
            except subprocess.TimeoutExpired as error:
                message = "codex_exec_timed_out"
                raise CodexCliError(message) from error
            if completed.returncode != 0:
                message = f"codex_exec_failed:{completed.returncode}"
                raise CodexCliError(message)
            try:
                return _JSON_OBJECT.validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                message = "codex_output_invalid"
                raise CodexCliError(message) from error


def resolve_codex_executable() -> Path | None:
    """Resolve an explicit operator override or the current user's PATH entry."""
    configured = os.environ.get("TRACE_CODEX_BIN")
    resolved = shutil.which(configured or "codex")
    return None if resolved is None else Path(resolved).expanduser().resolve()
