from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.tools.browser import BrowserTool
from ads_booster.tools.filesystem import FileListTool, FileReadTool, FileWriteTool
from ads_booster.tools.image_search import ImageSearchTool
from ads_booster.tools.image_view import ImageViewTool
from ads_booster.tools.models import Tool, ToolContext, ToolResult
from ads_booster.tools.shell import ShellTool
from ads_booster.tools.text_search import WebSearchTool
from ads_booster.tools.trace import TraceRunTool

if TYPE_CHECKING:
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


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
            ImageViewTool(),
            TraceRunTool(),
        )
    )
