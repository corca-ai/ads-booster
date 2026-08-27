from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ads_booster.transport.json_types import JsonValue

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.search.image.contracts import ImageSearchProvider
    from ads_booster.search.text.contracts import WebSearchProvider
    from ads_booster.transport.json_types import JsonObject


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


class ToolOutputText(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    type: Literal["input_text"] = "input_text"
    text: str = Field(min_length=1, max_length=5_000)


class ToolOutputImage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    type: Literal["input_image"] = "input_image"
    image_url: str = Field(min_length=1, max_length=24_000_000)
    detail: Literal["auto", "low", "high", "original"] = "auto"


_MODEL_OUTPUT_ADAPTER: TypeAdapter[list[JsonValue]] = TypeAdapter(list[JsonValue])


class ToolResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ok: bool
    output: str = Field(max_length=50_000)
    error_code: str | None = None
    model_output: tuple[ToolOutputText | ToolOutputImage, ...] = ()

    def model_output_json(self) -> list[JsonValue]:
        return _MODEL_OUTPUT_ADAPTER.validate_python(
            [item.model_dump(mode="json") for item in self.model_output]
        )


class Tool(Protocol):
    name: ClassVar[str]

    def descriptor(self) -> ToolDescriptor: ...

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult: ...
