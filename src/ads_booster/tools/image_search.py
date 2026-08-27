from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.search.image.contracts import ImageSearchError
from ads_booster.tools.models import ToolResult

if TYPE_CHECKING:
    from ads_booster.tools.models import ToolContext
    from ads_booster.transport.json_types import JsonObject


class _ImageSearchArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=1_000)
    max_results: int = Field(default=5, ge=1, le=10)


@dataclass(frozen=True, slots=True)
class ImageSearchTool:
    name: ClassVar[str] = "image_search"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Search the live web for images and return image URLs, thumbnails, and "
                "source pages; read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 1_000},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _ImageSearchArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False,
                output="image search requires a query and max_results between 1 and 10",
                error_code="invalid_arguments",
            )
        query = parsed.query.strip()
        if not query:
            return ToolResult(
                ok=False, output="image search query is empty", error_code="invalid_arguments"
            )
        if context.image_search is None:
            return ToolResult(
                ok=False,
                output="image search provider is not configured",
                error_code="image_search_unavailable",
            )
        try:
            response = context.image_search.search(query, parsed.max_results)
        except ImageSearchError as error:
            return ToolResult(ok=False, output=str(error), error_code=error.code)
        return ToolResult(ok=True, output=response.model_dump_json(ensure_ascii=False))
