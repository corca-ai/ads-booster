from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest
from PIL import Image

from ads_booster.search.image.background import BackgroundSearchError, ImageSearchBackgroundFetcher
from ads_booster.search.image.contracts import (
    ImageSearchError,
    ImageSearchResponse,
    ImageSearchResult,
)
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_IMAGE_MODEL_CALL_MESSAGE = "background search must not call an image model"
_POST_MESSAGE = "background search must not post data"
_EXPECTED_MAX_RESULTS = 25
_PROVIDER_DOWN_CODE = "image_search_unavailable"
_PROVIDER_DOWN_MESSAGE = "provider is down"


@dataclass(frozen=True, slots=True)
class _SearchFixture:
    response: ImageSearchResponse

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        # The query reaches the provider as written. Appending a "site:" operator restricted
        # nothing against live search and distorted the query, so it is gone.
        assert "site:" not in query
        assert max_results == _EXPECTED_MAX_RESULTS
        return self.response


@dataclass(frozen=True, slots=True)
class _FailingSearchFixture:
    def search(self, query: str, max_results: int) -> NoReturn:
        del query, max_results
        raise ImageSearchError(_PROVIDER_DOWN_CODE, _PROVIDER_DOWN_MESSAGE)


@dataclass(frozen=True, slots=True)
class _HttpFixture:
    responses: dict[str, HttpResponse]

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        assert "image" in headers["Accept"]
        return self.responses[url]

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> NoReturn:
        del url, payload, headers
        raise AssertionError(_IMAGE_MODEL_CALL_MESSAGE)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NoReturn:
        del url, form, headers
        raise AssertionError(_POST_MESSAGE)


def _png_bytes(
    size: tuple[int, int] = (720, 1280),
    color: tuple[int, int, int] = (24, 48, 96),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _result(
    image_url: str,
    source_url: str,
    size: tuple[int, int] | None = None,
) -> ImageSearchResult:
    return ImageSearchResult(
        title="candidate",
        image_url=image_url,
        thumbnail_url=image_url,
        source_url=source_url,
        width=None if size is None else size[0],
        height=None if size is None else size[1],
    )


def _response(*results: ImageSearchResult, query: str = "쿠로미 배경화면") -> ImageSearchResponse:
    return ImageSearchResponse(provider="fixture-search", query=query, results=results)


def test_background_fetcher_when_first_result_is_invalid_then_it_saves_next_valid_search_image(
    tmp_path: Path,
) -> None:
    # Given image search results with one invalid response before a portrait photo
    invalid_url = "https://images.example/invalid"
    valid_url = "https://images.example/background.png"
    response = _response(
        _result(invalid_url, "https://www.freepik.com/invalid"),
        _result(valid_url, "https://www.pexels.com/photo/background"),
        query="student study vertical photo",
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture(
            {
                invalid_url: HttpResponse(200, b"not an image", {}),
                valid_url: HttpResponse(200, _png_bytes(), {}),
            }
        ),
    )

    # When the generation path fetches a background
    background = fetcher.fetch(response.query, tmp_path / "inputs" / "background.png")
    provenance = tmp_path / "inputs" / "background-source.json"
    background.write_provenance(provenance)

    # Then it retains only the validated image and its source provenance
    assert background.path.is_file()
    assert background.image_url == valid_url
    assert json.loads(provenance.read_text(encoding="utf-8"))["source_url"] == (
        "https://www.pexels.com/photo/background"
    )


def test_background_fetcher_accepts_a_wallpaper_from_a_source_outside_the_stock_sites(
    tmp_path: Path,
) -> None:
    # Given the only candidate comes from an ordinary wallpaper site rather than free stock
    image_url = "https://kr.best-wallpaper.net/wallpaper/kuromi.jpg"
    response = _response(_result(image_url, "https://kr.best-wallpaper.net/kuromi"))
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({image_url: HttpResponse(200, _png_bytes((1125, 1939)), {})}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker requests the background
    background = fetcher.fetch(response.query, destination)

    # Then it is kept: a source allowlist of the free stock sites was measured against live
    # searches, discarded 65% of every candidate, and selected for landscape desktop
    # photography over the portrait wallpapers a lock screen actually needs.
    assert background.image_url == image_url
    assert destination.is_file()


def test_background_fetcher_when_the_search_provider_fails_then_no_artifact_is_written(
    tmp_path: Path,
) -> None:
    # Given the image search provider is unavailable
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_FailingSearchFixture(),
        http=_HttpFixture({}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker requests a background
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch("쿠로미 배경화면", destination)

    # Then the caller sees the no-usable-image contract and nothing reaches the job root
    assert failure.value.code == "background_search_no_usable_image"
    assert not destination.exists()


def test_background_fetcher_selects_the_best_lock_screen_candidate_from_one_search(
    tmp_path: Path,
) -> None:
    # Given one search returning candidates of different shapes and resolutions
    landscape_url = "https://images.example/landscape.png"
    lock_screen_url = "https://images.example/lock-screen.png"
    portrait_url = "https://images.example/portrait.png"
    sizes = {
        landscape_url: (2000, 1200),
        lock_screen_url: (1200, 2600),
        portrait_url: (1800, 2400),
    }
    response = _response(
        _result(landscape_url, "https://www.pexels.com/photo/landscape", sizes[landscape_url]),
        _result(lock_screen_url, "https://unsplash.com/photos/lock-screen", sizes[lock_screen_url]),
        _result(portrait_url, "https://pixabay.com/photos/portrait-1", sizes[portrait_url]),
        query="quiet mountain sunrise",
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture(
            {url: HttpResponse(200, _png_bytes(size), {}) for url, size in sizes.items()}
        ),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker selects a searched background
    background = fetcher.fetch("quiet mountain sunrise", destination)

    # Then it prefers the high-resolution portrait nearest the lock-screen aspect ratio
    assert background.image_url == lock_screen_url
    assert background.query == "quiet mountain sunrise"
    with Image.open(destination) as image:
        assert image.size == (1200, 2600)


def test_background_fetcher_keeps_a_phone_sized_wallpaper_below_the_general_floor(
    tmp_path: Path,
) -> None:
    # Given the only candidate is narrower than the general 640px floor but phone shaped
    image_url = "https://images.example/kuromi-phone.png"
    response = _response(_result(image_url, "https://kr.best-wallpaper.net/kuromi"))
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({image_url: HttpResponse(200, _png_bytes((474, 1026)), {})}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker selects a background
    background = fetcher.fetch(response.query, destination)

    # Then it survives: an image authored at phone width for a phone screen was being
    # rejected for being exactly the shape the lock screen wants.
    assert background.image_url == image_url
    with Image.open(destination) as image:
        assert image.size == (474, 1026)


@pytest.mark.parametrize(
    ("size", "reason"),
    [
        ((500, 889), "portrait but 16:9, so it is judged on the general floor"),
        ((318, 690), "lock-screen shaped but too few pixels to fill the screen"),
        ((600, 400), "small landscape article photography"),
    ],
)
def test_background_fetcher_rejects_images_too_small_for_a_lock_screen(
    tmp_path: Path,
    size: tuple[int, int],
    reason: str,
) -> None:
    # Given the only candidate is too small to fill a lock screen
    del reason
    image_url = "https://images.example/too-small.png"
    response = _response(_result(image_url, "https://kr.best-wallpaper.net/small"))
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({image_url: HttpResponse(200, _png_bytes(size), {})}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker requests a background
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch(response.query, destination)

    # Then nothing is written
    assert failure.value.code == "background_search_no_usable_image"
    assert not destination.exists()


def test_background_fetcher_skips_unsplash_plus_watermarked_previews(tmp_path: Path) -> None:
    # Given a premium Unsplash preview before a usable photo
    premium_url = "https://plus.unsplash.com/premium_photo-watermarked"
    free_url = "https://images.pexels.com/photos/free-photo.jpeg"
    response = _response(
        _result(premium_url, "https://unsplash.com/s/photos/misty-coast"),
        _result(free_url, "https://www.pexels.com/photo/free-photo"),
        query="misty coast",
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({free_url: HttpResponse(200, _png_bytes((1200, 2600)), {})}),
    )

    # When the worker selects a background
    background = fetcher.fetch("misty coast", tmp_path / "background.png")

    # Then a watermarked premium preview cannot become the artifact
    assert background.image_url == free_url


@pytest.mark.parametrize(
    ("title", "source_url", "reason"),
    [
        (
            "제주도 배경화면 일몰 석양",
            "https://www.crowdpic.net/photo/제주도-141244",
            "watermarked stock preview",
        ),
        (
            "김도영 스캠 첫 안타 직캠",
            "https://www.sportschosun.com/baseball/2025-02-22/2025",
            "press photography",
        ),
        (
            "간호사볼펜 의사펜 - 쿠팡",
            "https://www.coupang.com/vp/products/9114174310",
            "product listing",
        ),
        (
            "김도영 데뷔 첫 홈런 직캠 - YouTube",
            "https://www.youtube.com/watch?v=Mt6qq89",
            "video thumbnail",
        ),
        (
            "900개 이상 무료 노을 사진",
            "https://pixabay.com/ko/photos/search/노을/",
            "a listing, not one photo",
        ),
        ("고양이 배경화면 1920x1080", "https://kr.best-wallpaper.net/cat", "cut for a desktop"),
        (
            "기아 타이거즈 로고 배경화면",
            "https://kr.pinterest.com/pin/77757531049933364/",
            "a brand asset",
        ),
    ],
)
def test_background_fetcher_rejects_rows_that_are_not_wallpapers(
    tmp_path: Path,
    title: str,
    source_url: str,
    reason: str,
) -> None:
    # Given the only candidate is one of the shapes a live run showed is never a wallpaper
    del reason
    image_url = "https://images.example/candidate.png"
    response = _response(
        ImageSearchResult(
            title=title,
            image_url=image_url,
            thumbnail_url=image_url,
            source_url=source_url,
        )
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        # The row is rejected before anything is downloaded, so the fetcher must never
        # reach the transport: an empty response map would raise KeyError if it did.
        http=_HttpFixture({}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker requests a background
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch(response.query, destination)

    # Then the job fails rather than shipping it. A job with no background is one a person
    # looks at; a background that is somebody's press photo goes out silently.
    assert failure.value.code == "background_search_no_usable_image"
    assert not destination.exists()


def test_background_fetcher_keeps_a_wallpaper_site_that_merely_offers_a_download(
    tmp_path: Path,
) -> None:
    # Given an ordinary wallpaper site, whose titles carry the words a stock farm also uses
    image_url = "https://chiikawawallpaper.com/img/chiikawa.png"
    response = _response(
        ImageSearchResult(
            title="치이카와 배경화면(chiikawa wallpaper) 무료 다운로드",
            image_url=image_url,
            thumbnail_url=image_url,
            source_url="https://chiikawawallpaper.com/ko",
        )
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({image_url: HttpResponse(200, _png_bytes((1200, 2600)), {})}),
    )

    # When the worker selects a background
    background = fetcher.fetch(response.query, tmp_path / "background.png")

    # Then it survives: rejecting on "무료 다운로드" and "스톡" was measured against live
    # results and threw away cherry blossom and sunset wallpapers to catch one logo.
    assert background.image_url == image_url
