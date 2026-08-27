# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject, JsonValue


def provider_tools(tools: tuple[ToolDescriptor, ...]) -> tuple[ToolDescriptor, ...]:
    return tuple(
        tool.model_copy(update={"parameters": _strict_schema(tool.parameters)})
        if tool.strict
        else tool
        for tool in tools
    )


def _strict_schema(schema: JsonObject) -> JsonObject:
    normalized = {key: _strict_schema_value(value) for key, value in schema.items()}
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False
    return normalized


def _strict_schema_value(value: JsonValue) -> JsonValue:
    match value:
        case dict() as schema:
            return _strict_schema(schema)
        case list() as values:
            return [_strict_schema_value(item) for item in values]
        case None:
            return None
        case (bool() | int() | float() | str()) as scalar:
            return scalar
        case unreachable:
            assert_never(unreachable)
