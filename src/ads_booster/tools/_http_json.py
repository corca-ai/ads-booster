from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class HttpResponse(Protocol):
    def read(self) -> bytes: ...


_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def post_json(
    opener: Callable[..., HttpResponse],
    url: str,
    payload: JsonObject,
    headers: Mapping[str, str],
) -> JsonObject:
    if urlsplit(url).scheme != "https":
        raise ValueError("tool_endpoint_must_be_https")
    request = Request(  # noqa: S310 - all adapter endpoints are HTTPS and operator-owned.
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"accept": "application/json", "content-type": "application/json", **headers},
        method="POST",
    )
    try:
        response = opener(request, timeout=30.0)
        raw = cast("object", json.loads(response.read()))
        return _JSON_OBJECT.validate_python(raw)
    except HTTPError as error:
        raise ValueError(f"tool_endpoint_http_{error.code}") from error
