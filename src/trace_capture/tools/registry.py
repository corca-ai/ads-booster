from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trace_capture.tools.browser import BrowserTool
from trace_capture.tools.filesystem import FileListTool, FileReadTool, FileWriteTool
from trace_capture.tools.image_search import ImageSearchTool
from trace_capture.tools.models import Tool, ToolContext, ToolResult
from trace_capture.tools.shell import ShellTool
from trace_capture.tools.text_search import WebSearchTool
from trace_capture.tools.trace import TraceRunTool

if TYPE_CHECKING:
    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ToolRegistry:
    tools: tuple[Tool, ...]

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(tool.descriptor() for tool in self.tools)

    def execute(self, name: str, arguments: JsonObject, context: ToolContext) -> ToolResult:
        for tool in self.tools:
            if tool.name == name:
                if context.events is not None:
                    context.events.tool_started(name, arguments)
                result = tool.execute(arguments, context)
                if context.events is not None:
                    context.events.tool_finished(
                        name,
                        result.ok,
                        result.error_code,
                        len(result.output),
                    )
                return result
        result = ToolResult(ok=False, output=f"unknown tool: {name}", error_code="tool_not_found")
        if context.events is not None:
            context.events.tool_finished(name, result.ok, result.error_code, len(result.output))
        return result


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            FileReadTool(),
            FileListTool(),
            FileWriteTool(),
            ShellTool(),
            BrowserTool(),
            WebSearchTool(),
            ImageSearchTool(),
            TraceRunTool(),
        )
    )
