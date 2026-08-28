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
class _StructuredTurn:
    prompt: str
    schema: Mapping[str, object]
    workspace: Path
    sandbox: str
    timeout_seconds: float
    images: tuple[Path, ...]
    error_prefix: str


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
            return self._run_structured(
                _StructuredTurn(
                    prompt=prompt,
                    schema=schema,
                    workspace=Path(directory),
                    sandbox="read-only",
                    timeout_seconds=self.timeout_seconds,
                    images=images,
                    error_prefix="codex_exec",
                )
            )

    def run_appium_job(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        return self._run_structured(
            _StructuredTurn(
                prompt=prompt,
                schema=schema,
                workspace=workspace,
                sandbox="danger-full-access",
                timeout_seconds=timeout_seconds,
                images=(),
                error_prefix="codex_appium_job",
            )
        )

    def _run_structured(self, turn: _StructuredTurn) -> JsonObject:
        if not turn.workspace.is_dir():
            message = f"{turn.error_prefix}_workspace_unavailable"
            raise CodexCliError(message)
        with tempfile.TemporaryDirectory(prefix="trace-codex-") as directory:
            root = Path(directory)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            _ = schema_path.write_text(
                json.dumps(turn.schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            command = [str(self.executable), "exec"]
            if turn.images:
                command.extend(("--image", *(str(path) for path in turn.images)))
            command.extend(
                (
                    "--ephemeral",
                    "--sandbox",
                    turn.sandbox,
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "--cd",
                    str(turn.workspace.resolve()),
                )
            )
            if self.model is not None:
                command.extend(("--model", self.model))
            command.append("-")
            try:
                completed = subprocess.run(  # noqa: S603
                    command,
                    input=turn.prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=turn.timeout_seconds,
                )
            except OSError as error:
                message = f"{turn.error_prefix}_unavailable"
                raise CodexCliError(message) from error
            except subprocess.TimeoutExpired as error:
                message = f"{turn.error_prefix}_timed_out"
                raise CodexCliError(message) from error
            if completed.returncode != 0:
                message = f"{turn.error_prefix}_failed:{completed.returncode}"
                raise CodexCliError(message)
            try:
                return _JSON_OBJECT.validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                message = f"{turn.error_prefix}_output_invalid"
                raise CodexCliError(message) from error


def resolve_codex_executable() -> Path | None:
    """Resolve an explicit operator override or the current user's PATH entry."""
    configured = os.environ.get("TRACE_CODEX_BIN")
    resolved = shutil.which(configured or "codex")
    return None if resolved is None else Path(resolved).expanduser().resolve()
