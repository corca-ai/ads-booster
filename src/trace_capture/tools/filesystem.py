from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trace_capture.contracts.tools import ToolDescriptor
from trace_capture.tools.models import ToolContext, ToolResult
from trace_capture.tools.paths import resolve_workspace_path

if TYPE_CHECKING:
    from trace_capture.transport.json_types import JsonObject


class _PathArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: str = Field(default=".", min_length=1, max_length=1_024)


class _WriteArgs(_PathArgs):
    content: str = Field(max_length=1_000_000)


@dataclass(frozen=True, slots=True)
class FileReadTool:
    name: ClassVar[str] = "file_read"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="Read a UTF-8 text file inside the agent workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _PathArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(ok=False, output="path is required", error_code="invalid_arguments")
        path = resolve_workspace_path(context.workspace, parsed.path)
        if path is None or not path.is_file():
            return ToolResult(
                ok=False,
                output="file is outside the workspace or unavailable",
                error_code="path_denied",
            )
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return ToolResult(
                ok=False, output=f"file could not be read: {error}", error_code="file_read_failed"
            )
        return ToolResult(ok=True, output=content[:50_000])


@dataclass(frozen=True, slots=True)
class FileListTool:
    name: ClassVar[str] = "file_list"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="List files and directories inside the agent workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _PathArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(ok=False, output="path is invalid", error_code="invalid_arguments")
        path = resolve_workspace_path(context.workspace, parsed.path)
        if path is None or not path.is_dir():
            return ToolResult(
                ok=False,
                output="directory is outside the workspace or unavailable",
                error_code="path_denied",
            )
        try:
            entries = sorted(
                item.relative_to(context.workspace).as_posix() for item in path.iterdir()
            )
        except OSError as error:
            return ToolResult(
                ok=False,
                output=f"directory could not be listed: {error}",
                error_code="file_list_failed",
            )
        return ToolResult(ok=True, output="\n".join(entries[:500]))


@dataclass(frozen=True, slots=True)
class FileWriteTool:
    name: ClassVar[str] = "file_write"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="Write UTF-8 text to a file inside the agent workspace after approval.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _WriteArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False, output="path and content are required", error_code="invalid_arguments"
            )
        path = resolve_workspace_path(context.workspace, parsed.path)
        if path is None:
            return ToolResult(
                ok=False, output="path is outside the workspace", error_code="path_denied"
            )
        if not context.approval.request("file_write", parsed.path):
            return ToolResult(
                ok=False, output="file write was denied", error_code="approval_denied"
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(parsed.content, encoding="utf-8")
        except OSError as error:
            return ToolResult(
                ok=False,
                output=f"file could not be written: {error}",
                error_code="file_write_failed",
            )
        return ToolResult(ok=True, output=f"wrote {path.relative_to(context.workspace).as_posix()}")
