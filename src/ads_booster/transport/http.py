from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

import httpx2
from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

logger = logging.getLogger(__name__)

_LIMITS = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
_SOCKET_OPTIONS = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: Mapping[str, str]

    def json_object(self) -> JsonObject:
        return _JSON_OBJECT.validate_json(self.content)


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
    ) -> HttpResponse: ...

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse: ...

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse: ...


class HttpxClient:
    def __init__(self, read_timeout: float | None = None) -> None:
        """Create the shared client, widening only the read timeout when a call is slow."""
        transport = httpx2.HTTPTransport(
            http2=True,
            retries=3,
            limits=_LIMITS,
            socket_options=_SOCKET_OPTIONS,
        )
        self._client: httpx2.Client = httpx2.Client(
            transport=transport,
            timeout=_TIMEOUT if read_timeout is None else _read_timeout(read_timeout),
            follow_redirects=True,
            event_hooks={"request": [_log_request], "response": [_log_response]},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        response = self._client.get(url, headers=dict(headers))
        return HttpResponse(response.status_code, response.content, dict(response.headers))

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        response = self._client.post(url, json=payload, headers=dict(headers))
        return HttpResponse(response.status_code, response.content, dict(response.headers))

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        response = self._client.post(url, data=dict(form), headers=dict(headers))
        return HttpResponse(response.status_code, response.content, dict(response.headers))


def create_http_client(read_timeout: float | None = None) -> HttpxClient:
    return HttpxClient(read_timeout)


def _read_timeout(read_timeout: float) -> httpx2.Timeout:
    return httpx2.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=10.0)


def _log_request(request: httpx2.Request) -> None:
    logger.debug("HTTP request %s %s", request.method, request.url)


def _log_response(response: httpx2.Response) -> None:
    logger.debug("HTTP response %s %s", response.request.method, response.status_code)
