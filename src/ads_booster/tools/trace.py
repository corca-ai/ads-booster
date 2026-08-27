from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.tools.models import ToolContext, ToolResult
from ads_booster.tools.paths import resolve_workspace_path

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


class _TraceRunArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    job: str = Field(min_length=1, max_length=1_024)
    state_root: str = Field(default=".trace-runs", min_length=1, max_length=1_024)
    capture_output_root: str = Field(default="appium/outputs", min_length=1, max_length=1_024)
    appium_server: str = Field(default="http://127.0.0.1:4723", min_length=1, max_length=256)
    timeout_seconds: float = Field(default=120.0, gt=0, le=3_600)


@dataclass(frozen=True, slots=True)
class TraceRunTool:
    name: ClassVar[str] = "trace_run"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Run the existing Trace capture, staging, and composition workflow after approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "job": {"type": "string"},
                    "state_root": {"type": "string", "default": ".trace-runs"},
                    "capture_output_root": {"type": "string", "default": "appium/outputs"},
                    "appium_server": {"type": "string", "default": "http://127.0.0.1:4723"},
                    "timeout_seconds": {"type": "number", "default": 120, "maximum": 3_600},
                },
                "required": ["job"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _TraceRunArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False, output="TraceRun job is required", error_code="invalid_arguments"
            )
        job = resolve_workspace_path(context.workspace, parsed.job)
        state_root = resolve_workspace_path(context.workspace, parsed.state_root)
        capture_root = resolve_workspace_path(context.workspace, parsed.capture_output_root)
        if job is None or state_root is None or capture_root is None:
            return ToolResult(
                ok=False,
                output="TraceRun paths must stay inside the workspace",
                error_code="path_denied",
            )
        if not context.approval.request("trace_run", parsed.job):
            return ToolResult(ok=False, output="TraceRun was denied", error_code="approval_denied")
        command = shutil.which("trace-run")
        if command is None:
            return ToolResult(
                ok=False, output="trace-run is not installed", error_code="trace_run_unavailable"
            )
        argv = [
            command,
            "--job",
            str(job),
            "--state-root",
            str(state_root),
            "--capture-output-root",
            str(capture_root),
            "--appium-server",
            parsed.appium_server,
            "--timeout-seconds",
            str(parsed.timeout_seconds),
        ]
        try:
            completed = subprocess.run(
                tuple(argv),
                cwd=context.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=parsed.timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output="TraceRun timed out", error_code="trace_run_timeout")
        except OSError as error:
            return ToolResult(
                ok=False, output=f"TraceRun could not start: {error}", error_code="trace_run_failed"
            )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0:
            return ToolResult(ok=False, output=output[-50_000:], error_code="trace_run_failed")
        return ToolResult(ok=True, output=output[-50_000:])
