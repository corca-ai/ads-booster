from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trace_capture.contracts.tools import ToolDescriptor
from trace_capture.tools.models import ToolContext, ToolResult
from trace_capture.tools.paths import resolve_workspace_path

if TYPE_CHECKING:
    from trace_capture.transport.json_types import JsonObject


class _ShellArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    command: str = Field(min_length=1, max_length=10_000)
    cwd: str = Field(default=".", min_length=1, max_length=1_024)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


@dataclass(frozen=True, slots=True)
class ShellTool:
    name: ClassVar[str] = "shell_exec"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="Run a shell command inside the agent workspace after approval.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "default": "."},
                    "timeout_seconds": {"type": "number", "default": 30, "maximum": 300},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _ShellArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False, output="command is required", error_code="invalid_arguments"
            )
        cwd = resolve_workspace_path(context.workspace, parsed.cwd)
        if cwd is None or not cwd.is_dir():
            return ToolResult(
                ok=False,
                output="cwd is outside the workspace or unavailable",
                error_code="path_denied",
            )
        if not context.approval.request("shell_exec", parsed.command):
            return ToolResult(
                ok=False, output="shell command was denied", error_code="approval_denied"
            )
        shell = os.environ.get("SHELL", "/bin/sh")
        try:
            completed = subprocess.run(
                (shell, "-lc", parsed.command),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=parsed.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                ok=False, output="shell command timed out", error_code="shell_timeout"
            )
        except OSError as error:
            return ToolResult(
                ok=False,
                output=f"shell command failed to start: {error}",
                error_code="shell_start_failed",
            )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0:
            return ToolResult(
                ok=False, output=output[-50_000:], error_code=f"exit_{completed.returncode}"
            )
        return ToolResult(ok=True, output=output[-50_000:])
