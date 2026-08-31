from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest
from PIL import Image

from ads_booster.search.image.background import BackgroundSearchError, ImageSearchBackgroundFetcher
from ads_booster.search.image.contracts import ImageSearchResponse, ImageSearchResult
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_IMAGE_MODEL_CALL_MESSAGE = "background search must not call an image model"
_POST_MESSAGE = "background search must not post data"


@dataclass(frozen=True, slots=True)
class _SearchFixture:
    response: ImageSearchResponse

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        assert query.endswith(("site:pexels.com", "site:unsplash.com", "site:pixabay.com"))
        assert max_results == 5
        return self.response


@dataclass(frozen=True, slots=True)
class _UnsafeSourceSearchFixture:
    response: ImageSearchResponse

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        assert max_results == 5
        assert query.endswith(("site:pexels.com", "site:unsplash.com", "site:pixabay.com"))
        return self.response


@dataclass(frozen=True, slots=True)
class _DomainSearchFixture:
    responses: Mapping[str, ImageSearchResponse]

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        assert max_results == 5
        return self.responses[query]


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


def test_background_fetcher_when_first_result_is_invalid_then_it_saves_next_valid_search_image(
    tmp_path: Path,
) -> None:
    # Given image search results with one invalid response before a portrait photo
    invalid_url = "https://images.example/invalid"
    valid_url = "https://images.example/background.png"
    response = ImageSearchResponse(
        provider="fixture-search",
        query="student study vertical photo",
        results=(
            ImageSearchResult(
                title="invalid",
                image_url=invalid_url,
                thumbnail_url=invalid_url,
                source_url="https://www.freepik.com/invalid",
            ),
            ImageSearchResult(
                title="background",
                image_url=valid_url,
                thumbnail_url=valid_url,
                source_url="https://www.pexels.com/photo/background",
            ),
        ),
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

    # Then it retains only the validated approved image and its public source provenance
    assert background.path.is_file()
    assert background.image_url == valid_url
    assert json.loads(provenance.read_text(encoding="utf-8"))["source_url"] == (
        "https://www.pexels.com/photo/background"
    )


def test_background_fetcher_when_source_is_unapproved_then_it_fails_without_writing_an_artifact(
    tmp_path: Path,
) -> None:
    # Given a search response whose only image comes from an unapproved host
    image_url = "https://images.example/unsafe-background.jpg"
    response = ImageSearchResponse(
        provider="fixture-search",
        query="student study vertical photo",
        results=(
            ImageSearchResult(
                title="unsafe",
                image_url=image_url,
                thumbnail_url=image_url,
                source_url="https://www.freepik.com/unsafe-background",
            ),
        ),
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_UnsafeSourceSearchFixture(response),
        http=_HttpFixture({}),
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker requests the background through the allowlisted fetcher
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch(response.query, destination)

    # Then no downloaded artifact can reach the worker's execution boundary
    assert failure.value.code == "background_search_no_usable_image"
    assert not destination.exists()


def test_background_fetcher_selects_the_best_lock_screen_candidate_across_domains(
    tmp_path: Path,
) -> None:
    # Given usable results from all approved domains with different shapes and resolutions
    query = "quiet mountain sunrise"
    candidates = (
        (
            "pexels.com",
            "https://images.example/landscape.png",
            "https://www.pexels.com/photo/landscape",
            (2000, 1200),
        ),
        (
            "unsplash.com",
            "https://images.example/lock-screen.png",
            "https://unsplash.com/photos/lock-screen",
            (1200, 2600),
        ),
        (
            "pixabay.com",
            "https://images.example/portrait.png",
            "https://pixabay.com/photos/portrait-1",
            (1800, 2400),
        ),
    )
    responses = {
        f"{query} site:{domain}": ImageSearchResponse(
            provider="fixture-search",
            query=f"{query} site:{domain}",
            results=(
                ImageSearchResult(
                    title=domain,
                    image_url=image_url,
                    thumbnail_url=image_url,
                    source_url=source_url,
                    width=size[0],
                    height=size[1],
                ),
            ),
        )
        for domain, image_url, source_url, size in candidates
    }
    http = _HttpFixture(
        {
            image_url: HttpResponse(200, _png_bytes(size), {})
            for _domain, image_url, _source_url, size in candidates
        }
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_DomainSearchFixture(responses),
        http=http,
    )
    destination = tmp_path / "inputs" / "background.png"

    # When the hosted worker selects a searched background
    background = fetcher.fetch(query, destination)

    # Then it prefers the high-resolution portrait nearest the lock-screen aspect ratio
    assert background.image_url == "https://images.example/lock-screen.png"
    assert background.query == "quiet mountain sunrise site:unsplash.com"
    with Image.open(destination) as image:
        assert image.size == (1200, 2600)


def test_background_fetcher_skips_unsplash_plus_watermarked_previews(tmp_path: Path) -> None:
    # Given a premium Unsplash preview before a usable free stock photo
    premium_url = "https://plus.unsplash.com/premium_photo-watermarked"
    free_url = "https://images.pexels.com/photos/free-photo.jpeg"
    response = ImageSearchResponse(
        provider="fixture-search",
        query="misty coast",
        results=(
            ImageSearchResult(
                title="premium preview",
                image_url=premium_url,
                thumbnail_url=premium_url,
                source_url="https://unsplash.com/s/photos/misty-coast",
            ),
            ImageSearchResult(
                title="free photo",
                image_url=free_url,
                thumbnail_url=free_url,
                source_url="https://www.pexels.com/photo/free-photo",
            ),
        ),
    )
    fetcher = ImageSearchBackgroundFetcher(
        image_search=_SearchFixture(response),
        http=_HttpFixture({free_url: HttpResponse(200, _png_bytes((1200, 2600)), {})}),
    )

    # When the worker selects a background
    background = fetcher.fetch("student study vertical photo", tmp_path / "background.png")

    # Then a watermarked premium preview cannot become the artifact
    assert background.image_url == free_url
