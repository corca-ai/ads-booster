from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Never, Protocol, final, override
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore2
import httpx2

from ads_booster.knowledge.extractors import MAX_INPUT_BYTES
from ads_booster.knowledge.source_fetch_transport import PinnedHTTPTransport, PinnedResolutionError

if TYPE_CHECKING:
    from collections.abc import Callable

_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_SUCCESS_START: Final = 200
_SUCCESS_END: Final = 300
_NOT_MODIFIED: Final = 304
_CLIENT_ERROR_START: Final = 400
_REQUEST_TIMEOUT: Final = 408
_RATE_LIMITED: Final = 429
_NOT_FOUND: Final = 404
_SERVER_ERROR_START: Final = 500
_DENIED_HOSTS: Final = frozenset({"localhost", "metadata.google.internal"})
_METADATA_IPS: Final = frozenset({ipaddress.ip_address("169.254.169.254")})


@dataclass(slots=True)
class SourceFetchError(Exception):
    code: str
    retryable: bool = False

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class SourceFetchConfig:
    maximum_bytes: int = MAX_INPUT_BYTES
    maximum_redirects: int = 5
    timeout_seconds: float = 30.0
    trusted_fixture_hosts: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class SourceFetchRequest:
    url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class FetchedSource:
    original_url: str
    final_url: str
    status_code: int
    body: bytes
    mime_type: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime
    not_modified: bool


class SourceFetcher(Protocol):
    def fetch(self, request: SourceFetchRequest) -> FetchedSource: ...


@final
class ScopedSourceFetcher:
    """Fetch a bounded public source while authorizing every redirect target."""

    def __init__(
        self,
        config: SourceFetchConfig | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        """Configure bounds and optional controlled-fixture network capabilities."""
        self._config = config or SourceFetchConfig()
        self._transport = transport
        self._resolver = resolver or _resolve_host

    def fetch(self, request: SourceFetchRequest) -> FetchedSource:
        original_url = _sanitize_url(request.url)
        current_url = original_url
        headers = {"Accept": "*/*", "User-Agent": "Trace-Knowledge-Fetch/1"}
        if request.etag is not None:
            headers["If-None-Match"] = request.etag
        if request.last_modified is not None:
            headers["If-Modified-Since"] = request.last_modified
        timeout = httpx2.Timeout(self._config.timeout_seconds)
        transport = self._transport or PinnedHTTPTransport(
            self._resolver,
            self._address_allowed,
            self._config.timeout_seconds,
        )
        try:
            with httpx2.Client(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for redirect_count in range(self._config.maximum_redirects + 1):
                    self._authorize_url(current_url)
                    with client.stream("GET", current_url, headers=headers) as response:
                        redirected = _redirect_url(
                            response,
                            current_url,
                            redirect_count,
                            self._config.maximum_redirects,
                        )
                        if redirected is not None:
                            current_url = redirected
                            continue
                        _raise_for_status(response.status_code)
                        return self._read_response(original_url, current_url, response)
        except SourceFetchError:
            raise
        except PinnedResolutionError as error:
            error_code = "url_target_forbidden"
            raise SourceFetchError(error_code) from error
        except httpx2.TimeoutException as error:
            error_code = "fetch_timeout"
            raise SourceFetchError(error_code, retryable=True) from error
        except (httpx2.HTTPError, httpcore2.NetworkError, httpcore2.ProtocolError) as error:
            error_code = "fetch_unavailable"
            raise SourceFetchError(error_code, retryable=True) from error
        error_code = "redirect_state_invalid"
        raise SourceFetchError(error_code)

    def _authorize_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            _fail("url_host_missing")
        normalized_host = host.rstrip(".").lower()
        trusted = normalized_host in self._config.trusted_fixture_hosts
        if not trusted and normalized_host in _DENIED_HOSTS:
            _fail("url_target_forbidden")
        try:
            addresses = self._resolver(normalized_host)
        except OSError as error:
            error_code = "url_resolution_failed"
            raise SourceFetchError(error_code, retryable=True) from error
        if not addresses:
            error_code = "url_resolution_failed"
            raise SourceFetchError(error_code, retryable=True)
        if not trusted and any(_address_forbidden(item) for item in addresses):
            _fail("url_target_forbidden")

    def _address_allowed(self, host: str, address: str) -> bool:
        normalized_host = host.rstrip(".").lower()
        return normalized_host in self._config.trusted_fixture_hosts or (
            normalized_host not in _DENIED_HOSTS and not _address_forbidden(address)
        )

    def _read_response(
        self,
        original_url: str,
        final_url: str,
        response: httpx2.Response,
    ) -> FetchedSource:
        declared_length = response.headers.get("content-length")
        if declared_length is not None:
            try:
                length = int(declared_length)
            except ValueError:
                _fail("content_length_invalid")
            if length > self._config.maximum_bytes:
                _fail("fetch_body_too_large")
        chunks: list[bytes] = []
        length = 0
        if response.status_code != _NOT_MODIFIED:
            for chunk in response.iter_bytes():
                length += len(chunk)
                if length > self._config.maximum_bytes:
                    _fail("fetch_body_too_large")
                chunks.append(chunk)
        media_type = response.headers.get("content-type", "application/octet-stream")
        media_type = media_type.partition(";")[0].strip().lower() or "application/octet-stream"
        return FetchedSource(
            original_url=original_url,
            final_url=final_url,
            status_code=response.status_code,
            body=b"".join(chunks),
            mime_type=media_type,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            fetched_at=datetime.now(UTC),
            not_modified=response.status_code == _NOT_MODIFIED,
        )


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        _fail("url_scheme_forbidden")
    if parsed.username is not None or parsed.password is not None:
        _fail("url_credentials_forbidden")
    if parsed.hostname is None:
        _fail("url_host_missing")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
    return urlunsplit(parsed)


def sanitize_persisted_url(url: str) -> str:
    """Remove request-only query and fragment data from stored URL metadata."""
    parsed = urlsplit(url)
    _userinfo, _separator, host_port = parsed.netloc.rpartition("@")
    return urlunsplit(parsed._replace(netloc=host_port, query="", fragment=""))


def _redirect_url(
    response: httpx2.Response,
    current_url: str,
    redirect_count: int,
    maximum_redirects: int,
) -> str | None:
    if response.status_code not in _REDIRECT_STATUSES:
        return None
    if redirect_count == maximum_redirects:
        _fail("redirect_limit_exceeded")
    location = response.headers.get("location")
    if location is None:
        _fail("redirect_location_missing")
    return _sanitize_url(urljoin(current_url, location))


def _raise_for_status(status_code: int) -> None:
    if _SUCCESS_START <= status_code < _SUCCESS_END or status_code == _NOT_MODIFIED:
        return
    if status_code in {401, 403}:
        _fail("http_access_denied")
    if status_code == _NOT_FOUND:
        _fail("http_not_found")
    if status_code == _REQUEST_TIMEOUT:
        _raise_retryable("http_request_timeout")
    if status_code == _RATE_LIMITED:
        _raise_retryable("http_rate_limited")
    if status_code >= _SERVER_ERROR_START:
        _raise_retryable("http_server_unavailable")
    if status_code >= _CLIENT_ERROR_START:
        _fail("http_client_error")
    _fail("http_status_unsupported")


def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(str(item[4][0]) for item in records))


def _address_forbidden(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return (
        parsed in _METADATA_IPS
        or not parsed.is_global
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def _fail(code: str) -> Never:
    raise SourceFetchError(code)


def _raise_retryable(code: str) -> Never:
    raise SourceFetchError(code, retryable=True)


__all__ = [
    "FetchedSource",
    "ScopedSourceFetcher",
    "SourceFetchConfig",
    "SourceFetchError",
    "SourceFetchRequest",
    "SourceFetcher",
    "sanitize_persisted_url",
]
