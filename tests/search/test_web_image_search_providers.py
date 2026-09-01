from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ads_booster.search.image.contracts import MAX_IMAGE_SEARCH_RESULTS, ImageSearchError
from ads_booster.search.image.providers import (
    BraveImageSearchProvider,
    DdgsImageSearchProvider,
    create_image_search_provider,
)
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingHttp:
    response: HttpResponse
    headers: dict[str, str] = field(default_factory=dict)

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        _ = url
        self.headers.clear()
        self.headers.update(headers)
        return self.response

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, payload, headers)
        message = "unexpected POST"
        raise AssertionError(message)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected POST"
        raise AssertionError(message)


def test_ddgs_image_provider_reads_cli_json_output_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a ddgs-compatible command that writes image JSON to the output path
    def locate(_name: str) -> str:
        return "/usr/bin/ddgs"

    def run(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        output_path = Path(argv[argv.index("--output") + 1])
        first = '[{"title":"Source","image":"https://cdn.example/image.jpg",'
        second = '"thumbnail":"https://cdn.example/thumb.jpg",'
        third = '"url":"https://example.com/post","width":1200,"height":800}]'
        _ = output_path.write_text(
            first + second + third,
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ads_booster.search.image.providers.shutil.which", locate)
    monkeypatch.setattr("ads_booster.search.image.providers.subprocess.run", run)

    # When the DDGS image provider runs
    response = DdgsImageSearchProvider(timeout_seconds=5).search("Trace", 1)

    # Then it returns normalized image and source URLs
    assert response.provider == "duckduckgo"
    assert response.results[0].image_url == "https://cdn.example/image.jpg"
    assert response.results[0].source_url == "https://example.com/post"
    assert response.results[0].width == 1200


def test_ddgs_image_provider_asks_for_tall_wallpaper_photos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a ddgs-compatible command that records how it was invoked
    seen: list[tuple[str, ...]] = []

    def locate(_name: str) -> str:
        return "/usr/bin/ddgs"

    def run(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        seen.append(argv)
        _ = Path(argv[argv.index("--output") + 1]).write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ads_booster.search.image.providers.shutil.which", locate)
    monkeypatch.setattr("ads_booster.search.image.providers.subprocess.run", run)

    # When an image search runs
    _ = DdgsImageSearchProvider(timeout_seconds=5).search("KIA 타이거즈 배경화면", 20)

    # Then the provider narrows open-web results to safe portrait wallpaper photography
    argv = seen[0]
    assert argv[argv.index("--size") + 1] == "Wallpaper"
    assert argv[argv.index("--layout") + 1] == "Tall"
    assert argv[argv.index("--type_image") + 1] == "photo"
    assert argv[argv.index("--safesearch") + 1] == "moderate"
    assert argv[argv.index("--query") + 1] == "KIA 타이거즈 배경화면"
    assert argv[argv.index("--max_results") + 1] == "20"


def test_image_providers_accept_the_contract_ceiling_and_refuse_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The downstream resolution gate, not the provider, is what thins the pool."""

    # Given a ddgs-compatible command that answers with no rows
    def locate(_name: str) -> str:
        return "/usr/bin/ddgs"

    def run(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        _ = Path(argv[argv.index("--output") + 1]).write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ads_booster.search.image.providers.shutil.which", locate)
    monkeypatch.setattr("ads_booster.search.image.providers.subprocess.run", run)
    provider = DdgsImageSearchProvider(timeout_seconds=5)

    # When the ceiling is requested, and then one more than the bound
    accepted = provider.search("Trace", MAX_IMAGE_SEARCH_RESULTS)
    bound = f"between 1 and {MAX_IMAGE_SEARCH_RESULTS}"
    with pytest.raises(ImageSearchError, match=bound):
        _ = provider.search("Trace", MAX_IMAGE_SEARCH_RESULTS + 1)

    # Then the ceiling is inside the contract and the bound is reported in its own terms.
    # Provider and response contract read it from one constant: when they disagreed, asking
    # for more rows than the response could hold raised a ValidationError no caller caught.
    assert accepted.results == ()
    with pytest.raises(ImageSearchError, match=bound):
        _ = BraveImageSearchProvider(
            http=RecordingHttp(HttpResponse(200, b"{}", {})), api_key="secret"
        ).search("Trace", MAX_IMAGE_SEARCH_RESULTS + 1)


def test_brave_image_provider_normalizes_properties_and_limits_results() -> None:
    # Given a Brave image response with properties and more results than requested
    first = b'{"results":['
    second = b'{"title":"One","properties":{"url":"https://cdn.example/one.jpg",'
    third = b'"width":1200,"height":800},"thumbnail":"https://thumb.example/one.jpg",'
    fourth = b'"url":"https://example.com/one","source":"Example"},'
    fifth = b'{"title":"Two","image":"https://cdn.example/two.jpg",'
    sixth = b'"thumbnail":"https://thumb.example/two.jpg",'
    seventh = b'"url":"https://example.com/two"}]}'
    http = RecordingHttp(
        HttpResponse(
            200,
            first + second + third + fourth + fifth + sixth + seventh,
            {},
        )
    )
    provider = BraveImageSearchProvider(http=http, api_key="secret")

    # When Brave image search is requested for one result
    response = provider.search("Trace", 1)

    # Then only one structured image result is returned without exposing the key
    assert "secret" not in response.model_dump_json()
    assert len(response.results) == 1
    assert response.results[0].image_url == "https://cdn.example/one.jpg"
    assert response.results[0].thumbnail_url == "https://thumb.example/one.jpg"
    assert response.results[0].height == 800
    assert http.headers["X-Subscription-Token"] == "secret"


def test_image_provider_selection_fails_closed_without_brave_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given explicit Brave image search without an API key
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    provider = create_image_search_provider(
        RecordingHttp(HttpResponse(200, b"{}", {})), "brave", 30
    )

    # When the provider is called
    with pytest.raises(ImageSearchError, match="BRAVE_SEARCH_API_KEY"):
        _ = provider.search("Trace", 5)
