from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, final, override, runtime_checkable

import httpcore2
import httpx2

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

type SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


@runtime_checkable
class _IterableStream(Protocol):
    def __iter__(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class PinnedResolutionError(Exception):
    host: str


@final
class _PinnedBackend(httpcore2.NetworkBackend):
    def __init__(
        self,
        resolver: Callable[[str], tuple[str, ...]],
        address_allowed: Callable[[str, str], bool],
    ) -> None:
        self._resolver = resolver
        self._address_allowed = address_allowed
        self._backend = httpcore2.SyncBackend()

    @override
    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.NetworkStream:
        addresses = self._resolver(host)
        allowed = tuple(item for item in addresses if self._address_allowed(host, item))
        if not addresses or len(allowed) != len(addresses):
            raise PinnedResolutionError(host)
        return self._backend.connect_tcp(
            allowed[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    @override
    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.NetworkStream:
        raise PinnedResolutionError(path)

    @override
    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


@final
class _ResponseStream(httpx2.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    @override
    def __iter__(self) -> Iterator[bytes]:
        return iter(self._stream)

    @override
    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


@final
class PinnedHTTPTransport(httpx2.BaseTransport):
    def __init__(
        self,
        resolver: Callable[[str], tuple[str, ...]],
        address_allowed: Callable[[str, str], bool],
        timeout_seconds: float,
    ) -> None:
        """Connect through the exact address set approved for the requested hostname."""
        self._pool = httpcore2.ConnectionPool(
            network_backend=_PinnedBackend(resolver, address_allowed),
            http1=True,
            http2=True,
            max_connections=20,
            max_keepalive_connections=5,
            keepalive_expiry=10.0,
        )
        self._timeout_seconds = timeout_seconds

    @override
    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if not isinstance(request.stream, httpx2.SyncByteStream):
            message = "sync_request_stream_required"
            raise TypeError(message)
        response = self._pool.handle_request(
            httpcore2.Request(
                method=request.method,
                url=httpcore2.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions={
                    "timeout": {
                        "connect": self._timeout_seconds,
                        "read": self._timeout_seconds,
                        "write": self._timeout_seconds,
                        "pool": self._timeout_seconds,
                    }
                },
            )
        )
        if not isinstance(response.stream, _IterableStream):
            message = "sync_response_stream_required"
            raise TypeError(message)
        return httpx2.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(_sync_stream(response.stream)),
        )

    @override
    def close(self) -> None:
        self._pool.close()


def _sync_stream(stream: object) -> _IterableStream:
    if isinstance(stream, _IterableStream):
        return stream
    message = "sync_response_stream_required"
    raise TypeError(message)


__all__ = ["PinnedHTTPTransport", "PinnedResolutionError"]
