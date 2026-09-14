from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue


def strict_completion_schema(schema: JsonObject) -> JsonObject:
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        message = "completion_schema_definitions_invalid"
        raise TypeError(message)

    result = _expand(schema, definitions, frozenset())
    if not isinstance(result, dict):
        message = "completion_schema_root_invalid"
        raise TypeError(message)
    return result


def _expand(value: JsonValue, definitions: JsonObject, resolving: frozenset[str]) -> JsonValue:
    match value:
        case dict() as mapping:
            reference = mapping.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                    message = "completion_schema_reference_invalid"
                    raise ValueError(message)
                name = reference.removeprefix("#/$defs/")
                resolved = definitions.get(name)
                if name in resolving or not isinstance(resolved, dict):
                    message = "completion_schema_reference_recursive"
                    raise ValueError(message)
                return _expand(resolved, definitions, resolving | {name})
            result = {
                key: _expand(item, definitions, resolving)
                for key, item in mapping.items()
                if key not in {"$defs", "default"}
            }
            properties = result.get("properties")
            if isinstance(properties, dict):
                result["required"] = list(properties)
                result["additionalProperties"] = False
            return result
        case list() as sequence:
            return [_expand(item, definitions, resolving) for item in sequence]
        case str() | int() | float() | None:
            return value
        case _:
            assert_never(value)
