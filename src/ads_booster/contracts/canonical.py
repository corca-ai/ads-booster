"""Cross-runtime canonical JSON used by portable Marketing Agent contracts."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue

_JAVASCRIPT_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def canonical_json(value: JsonObject) -> str:
    _reject_nonportable_numbers(value)
    return json.dumps(
        _utf16_sorted(value),
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: JsonObject) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _reject_nonportable_numbers(value: JsonValue) -> None:
    if isinstance(value, float):
        message = "portable_json_float_forbidden"
        raise TypeError(message)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > _JAVASCRIPT_MAX_SAFE_INTEGER
    ):
        message = "portable_json_integer_outside_safe_range"
        raise TypeError(message)
    if isinstance(value, list):
        for item in value:
            _reject_nonportable_numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_nonportable_numbers(item)


def _utf16_sorted(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_utf16_sorted(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _utf16_sorted(value[key])
            for key in sorted(value, key=lambda item: item.encode("utf-16-be"))
        }
    return value


__all__ = ["canonical_json", "canonical_sha256"]
