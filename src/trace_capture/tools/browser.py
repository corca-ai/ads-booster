from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trace_capture.contracts.tools import ToolDescriptor
from trace_capture.tools.models import ToolContext, ToolResult
from trace_capture.tools.paths import resolve_workspace_path

if TYPE_CHECKING:
    from trace_capture.transport.json_types import JsonObject


class _BrowserArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    action: Literal["open", "snapshot", "click", "type", "screenshot"]
    url: str | None = Field(default=None, max_length=4_096)
    ref: str | None = Field(default=None, max_length=128)
    text: str | None = Field(default=None, max_length=20_000)
    path: str | None = Field(default=None, max_length=1_024)


@dataclass(frozen=True, slots=True)
class BrowserTool:
    name: ClassVar[str] = "browser"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Navigate and inspect a browser with agent-browser; mutations require approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "snapshot", "click", "type", "screenshot"],
                    },
                    "url": {"type": "string"},
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _BrowserArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False, output="browser action is invalid", error_code="invalid_arguments"
            )
        match parsed.action:
            case "open":
                return self._open(parsed, context)
            case "snapshot":
                return self._run(("snapshot",), context)
            case "click":
                return self._click(parsed, context)
            case "type":
                return self._type(parsed, context)
            case "screenshot":
                return self._screenshot(parsed, context)

    def _open(self, parsed: _BrowserArgs, context: ToolContext) -> ToolResult:
        if parsed.url is None or urlsplit(parsed.url).scheme not in {"http", "https"}:
            return ToolResult(
                ok=False, output="browser URL must use http or https", error_code="invalid_url"
            )
        return self._run(("open", parsed.url), context)

    def _click(self, parsed: _BrowserArgs, context: ToolContext) -> ToolResult:
        if parsed.ref is None:
            return ToolResult(
                ok=False, output="browser click requires a ref", error_code="invalid_arguments"
            )
        if not context.approval.request("browser_click", parsed.ref):
            return ToolResult(
                ok=False, output="browser click was denied", error_code="approval_denied"
            )
        return self._run(("click", parsed.ref), context)

    def _type(self, parsed: _BrowserArgs, context: ToolContext) -> ToolResult:
        if parsed.ref is None or parsed.text is None:
            return ToolResult(
                ok=False,
                output="browser type requires ref and text",
                error_code="invalid_arguments",
            )
        if not context.approval.request("browser_type", parsed.ref):
            return ToolResult(
                ok=False, output="browser input was denied", error_code="approval_denied"
            )
        return self._run(("type", parsed.ref, parsed.text), context)

    def _screenshot(self, parsed: _BrowserArgs, context: ToolContext) -> ToolResult:
        if parsed.path is None:
            return ToolResult(
                ok=False, output="browser screenshot requires path", error_code="invalid_arguments"
            )
        destination = resolve_workspace_path(context.workspace, parsed.path)
        if destination is None:
            return ToolResult(
                ok=False,
                output="screenshot path is outside the workspace",
                error_code="path_denied",
            )
        if not context.approval.request("browser_screenshot", parsed.path):
            return ToolResult(
                ok=False, output="browser screenshot was denied", error_code="approval_denied"
            )
        return self._run(("screenshot", str(destination)), context)

    def _run(self, arguments: tuple[str, ...], context: ToolContext) -> ToolResult:
        if not context.browser_command:
            return ToolResult(
                ok=False,
                output="browser command is not configured",
                error_code="browser_unavailable",
            )
        try:
            completed = subprocess.run(
                (*context.browser_command, *arguments),
                cwd=context.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return ToolResult(
                ok=False,
                output="agent-browser is not installed or not on PATH",
                error_code="browser_unavailable",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                ok=False, output="browser command timed out", error_code="browser_timeout"
            )
        except OSError as error:
            return ToolResult(
                ok=False, output=f"browser command failed: {error}", error_code="browser_failed"
            )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0:
            return ToolResult(
                ok=False, output=output[-50_000:], error_code=f"exit_{completed.returncode}"
            )
        return ToolResult(ok=True, output=output[-50_000:])
