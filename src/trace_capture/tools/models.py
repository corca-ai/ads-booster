from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.search.image.contracts import ImageSearchProvider
    from trace_capture.search.text.contracts import WebSearchProvider
    from trace_capture.transport.json_types import JsonObject


class ApprovalPort(Protocol):
    def request(self, action: str, detail: str) -> bool: ...


class ToolEventSink(Protocol):
    def tool_started(self, name: str, arguments: JsonObject) -> None: ...

    def tool_finished(
        self,
        name: str,
        ok: bool,
        error_code: str | None,
        output_length: int,
    ) -> None: ...


@dataclass(slots=True)  # noqa: MUTABLE_OK
class ToolContext:
    """Mutable execution boundary for workspace, approval, and UI event ownership."""

    workspace: Path
    approval: ApprovalPort
    browser_command: tuple[str, ...]
    events: ToolEventSink | None = None
    web_search: WebSearchProvider | None = None
    image_search: ImageSearchProvider | None = None


class ToolResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ok: bool
    output: str = Field(max_length=50_000)
    error_code: str | None = None


class Tool(Protocol):
    name: ClassVar[str]

    def descriptor(self) -> ToolDescriptor: ...

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult: ...
