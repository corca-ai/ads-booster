from __future__ import annotations

import httpx2
import pytest

from ads_booster.knowledge.source_fetch import (
    ScopedSourceFetcher,
    SourceFetchConfig,
    SourceFetchError,
    SourceFetchRequest,
)


def test_fetch_preserves_final_url_status_validators_and_bytes_across_redirect() -> None:
    # Given
    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"Location": "https://cdn.example/final"})
        return httpx2.Response(
            200,
            content=b"source body",
            headers={"Content-Type": "text/plain; charset=utf-8", "ETag": '"v1"'},
        )

    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(route),
        resolver=lambda _host: ("93.184.216.34",),
    )

    # When
    fetched = fetcher.fetch(SourceFetchRequest(url="https://origin.example/start#private"))

    # Then
    assert (
        fetched.original_url,
        fetched.final_url,
        fetched.status_code,
        fetched.mime_type,
        fetched.etag,
        fetched.body,
    ) == (
        "https://origin.example/start",
        "https://cdn.example/final",
        200,
        "text/plain",
        '"v1"',
        b"source body",
    )


def test_conditional_fetch_returns_empty_not_modified_observation() -> None:
    # Given
    seen_headers: list[str] = []

    def unchanged(request: httpx2.Request) -> httpx2.Response:
        seen_headers.append(request.headers["if-none-match"])
        return httpx2.Response(304, headers={"ETag": '"v1"'})

    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(unchanged),
        resolver=lambda _host: ("93.184.216.34",),
    )

    # When
    fetched = fetcher.fetch(SourceFetchRequest(url="https://origin.example/source", etag='"v1"'))

    # Then
    assert fetched.not_modified is True
    assert fetched.body == b""
    assert seen_headers == ['"v1"']


@pytest.mark.parametrize(
    ("url", "resolved"),
    [
        ("file:///etc/passwd", ("93.184.216.34",)),
        ("http://127.0.0.1/private", ("127.0.0.1",)),
        ("http://169.254.169.254/latest/meta-data", ("169.254.169.254",)),
        ("http://service.internal/data", ("10.0.0.2",)),
    ],
)
def test_fetch_rejects_local_private_and_metadata_targets(
    url: str,
    resolved: tuple[str, ...],
) -> None:
    # Given
    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(200)),
        resolver=lambda _host: resolved,
    )

    # When / Then
    with pytest.raises(SourceFetchError, match=r"url_(scheme|target)_forbidden"):
        _ = fetcher.fetch(SourceFetchRequest(url=url))


def test_fetch_rejects_url_userinfo_before_request() -> None:
    # Given
    requested: list[str] = []

    def route(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200)

    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(route),
        resolver=lambda _host: ("93.184.216.34",),
    )

    # When / Then
    with pytest.raises(SourceFetchError, match="url_credentials_forbidden"):
        _ = fetcher.fetch(SourceFetchRequest(url="https://user:pass@public.example/source"))
    assert requested == []


def test_redirect_is_revalidated_before_private_target_is_requested() -> None:
    # Given
    requested: list[str] = []

    def redirect(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(redirect),
        resolver=lambda host: ("93.184.216.34",) if host == "origin.example" else ("127.0.0.1",),
    )

    # When / Then
    with pytest.raises(SourceFetchError, match="url_target_forbidden"):
        _ = fetcher.fetch(SourceFetchRequest(url="https://origin.example/start"))
    assert requested == ["https://origin.example/start"]


def test_connection_uses_second_resolution_only_after_reauthorizing_its_addresses() -> None:
    # Given
    answers = iter((("93.184.216.34",), ("127.0.0.1",)))
    fetcher = ScopedSourceFetcher(resolver=lambda _host: next(answers))

    # When / Then
    with pytest.raises(SourceFetchError) as caught:
        _ = fetcher.fetch(SourceFetchRequest(url="http://rebinding.example/source"))
    assert caught.value.code == "url_target_forbidden"


def test_fetch_stops_when_stream_exceeds_bound_without_echoing_url() -> None:
    # Given
    fetcher = ScopedSourceFetcher(
        SourceFetchConfig(maximum_bytes=3),
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(200, content=b"four", headers={"Content-Length": "4"})
        ),
        resolver=lambda _host: ("93.184.216.34",),
    )

    # When / Then
    with pytest.raises(SourceFetchError) as caught:
        _ = fetcher.fetch(SourceFetchRequest(url="https://origin.example/token-secret"))
    assert caught.value.code == "fetch_body_too_large"
    assert "token-secret" not in str(caught.value)


def test_fetch_reports_private_access_failure_without_reading_or_echoing_body() -> None:
    # Given
    fetcher = ScopedSourceFetcher(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(403, content=b"token=private-value")
        ),
        resolver=lambda _host: ("93.184.216.34",),
    )

    # When / Then
    with pytest.raises(SourceFetchError) as caught:
        _ = fetcher.fetch(SourceFetchRequest(url="https://origin.example/private-token"))
    assert caught.value.code == "http_access_denied"
    assert "private" not in str(caught.value)
