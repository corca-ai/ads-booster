from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.search.image.background import BackgroundSearchError
from ads_booster.search.image.contracts import (
    ImageSearchError,
    ImageSearchResponse,
    ImageSearchResult,
)
from ads_booster.search.image.open_background import OpenWebBackgroundFetcher
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_IMAGE_MODEL_CALL_MESSAGE = "background search must not call an image model"
_POST_MESSAGE = "background search must not post data"
_QUERY = "김도영 타격 직캠"
_JSON_OBJECT: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])


@dataclass(slots=True)
class _SearchFixture:
    """Answers the one open-web search call, recording exactly what was asked for."""

    response: ImageSearchResponse | None = None
    failure: ImageSearchError | None = None
    calls: list[tuple[str, int]] = field(default_factory=list)

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        self.calls.append((query, max_results))
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


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


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (24, 48, 96)).save(buffer, format="PNG")
    return buffer.getvalue()


def _result(name: str, image_url: str, source_url: str) -> ImageSearchResult:
    return ImageSearchResult(
        title=name,
        image_url=image_url,
        thumbnail_url=image_url,
        source_url=source_url,
    )


def test_open_web_background_when_earlier_hits_are_unusable_then_the_first_usable_one_wins(
    tmp_path: Path,
) -> None:
    # Given open-web results where only the third decodes at a portrait-friendly size
    broken = "https://cdn.example/broken"
    small = "https://cdn.example/small.png"
    usable = "https://fan.example/kimdoyoung.jpg"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(
                _result("broken", broken, "https://blog.example/post/1"),
                _result("small", small, "https://blog.example/post/2"),
                _result("usable", usable, "https://gall.example/board/3"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture(
            {
                broken: HttpResponse(200, b"not an image", {}),
                small: HttpResponse(200, _png_bytes(400, 700), {}),
                usable: HttpResponse(200, _png_bytes(1080, 1920), {}),
            }
        ),
    )

    # When the candidate image stage fetches a background
    destination = tmp_path / "inputs" / "background.png"
    background = fetcher.fetch(_QUERY, destination)
    provenance = tmp_path / "inputs" / "background-source.json"
    background.write_provenance(provenance)

    # Then the model-authored query ran unmodified against the open web
    assert search.calls == [(_QUERY, 8)]
    # And the stored background carries the page that published it
    assert background.path.is_file()
    assert background.image_url == usable
    assert background.source_url == "https://gall.example/board/3"
    assert background.provider == "duckduckgo"
    assert len(background.sha256) == 64
    recorded = _JSON_OBJECT.validate_json(provenance.read_bytes())
    assert recorded["source_url"] == "https://gall.example/board/3"
    assert recorded["query"] == _QUERY


def test_open_web_background_when_nothing_decodes_then_it_reports_an_exhausted_search(
    tmp_path: Path,
) -> None:
    # Given every open-web result failing to download or decode
    broken = "https://cdn.example/broken"
    missing = "https://cdn.example/missing"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="brave",
            query=_QUERY,
            results=(
                _result("broken", broken, "https://blog.example/post/1"),
                _result("missing", missing, "https://blog.example/post/2"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture(
            {
                broken: HttpResponse(200, b"not an image", {}),
                missing: HttpResponse(404, b"", {}),
            }
        ),
    )

    # When / Then the stage sees the exhausted-search code and no file is left behind
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch(_QUERY, tmp_path / "inputs" / "background.png")
    assert failure.value.code == "background_search_no_usable_image"
    assert not (tmp_path / "inputs" / "background.png").is_file()


def test_open_web_background_when_the_provider_fails_then_it_reports_a_search_failure(
    tmp_path: Path,
) -> None:
    # Given a provider that cannot answer at all
    search = _SearchFixture(
        failure=ImageSearchError("image_search_unavailable", "ddgs is not installed")
    )
    fetcher = OpenWebBackgroundFetcher(image_search=search, http=_HttpFixture({}))

    # When / Then the failure is typed as a search failure, not a local write failure
    with pytest.raises(BackgroundSearchError) as failure:
        _ = fetcher.fetch(_QUERY, tmp_path / "inputs" / "background.png")
    assert failure.value.code == "background_search_provider_failed"


def test_open_web_background_when_a_result_is_oversized_then_it_is_skipped(
    tmp_path: Path,
) -> None:
    # Given a first result larger than the download cap and a second within it
    oversized = "https://cdn.example/huge.png"
    usable = "https://cdn.example/ok.png"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(
                _result("huge", oversized, "https://blog.example/post/1"),
                _result("ok", usable, "https://blog.example/post/2"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture(
            {
                oversized: HttpResponse(200, b"x" * 300_000, {}),
                usable: HttpResponse(200, _png_bytes(900, 1600), {}),
            }
        ),
        maximum_bytes=200_000,
    )

    # When the stage fetches a background
    background = fetcher.fetch(_QUERY, tmp_path / "inputs" / "background.png")

    # Then the oversized candidate never reached the decoder
    assert background.image_url == usable


def test_collect_prefers_portrait_images_and_only_backfills_with_landscape() -> None:
    # Given two landscape hits ahead of two portrait ones
    wide_one = "https://cdn.example/wide-1.png"
    wide_two = "https://cdn.example/wide-2.png"
    tall = "https://cdn.example/tall.png"
    square = "https://cdn.example/square.png"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(
                _result("wide-1", wide_one, "https://a.example/1"),
                _result("wide-2", wide_two, "https://b.example/2"),
                _result("tall", tall, "https://c.example/3"),
                _result("square", square, "https://d.example/4"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture(
            {
                wide_one: HttpResponse(200, _png_bytes(1920, 1080), {}),
                wide_two: HttpResponse(200, _png_bytes(2000, 900), {}),
                tall: HttpResponse(200, _png_bytes(1080, 1920), {}),
                square: HttpResponse(200, _png_bytes(1200, 1200), {}),
            }
        ),
    )

    # When the judge pool is collected
    collected = fetcher.collect(_QUERY, 4)

    # Then the vertical and near-square images take the first slots
    assert [image.source_url for image in collected.images] == [
        "https://c.example/3",
        "https://d.example/4",
        "https://a.example/1",
        "https://b.example/2",
    ]
    assert [image.image_id for image in collected.images] == [
        "img-a",
        "img-b",
        "img-c",
        "img-d",
    ]


def test_collect_takes_at_most_two_images_from_one_source_domain() -> None:
    # Given four portrait hits, three of them from the same site
    same = [f"https://cdn.example/same-{index}.png" for index in range(3)]
    other = "https://cdn.example/other.png"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(
                _result("same-0", same[0], "https://www.gall.example/board/1"),
                _result("same-1", same[1], "https://gall.example/board/2"),
                _result("same-2", same[2], "https://gall.example/board/3"),
                _result("other", other, "https://blog.example/post/9"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture(
            {url: HttpResponse(200, _png_bytes(1080, 1920), {}) for url in [*same, other]}
        ),
    )

    # When the judge pool is collected
    collected = fetcher.collect(_QUERY, 6)

    # Then the third image from that domain never reaches the judge, www. and all
    assert [image.source_url for image in collected.images] == [
        "https://www.gall.example/board/1",
        "https://gall.example/board/2",
        "https://blog.example/post/9",
    ]


def test_collect_stops_after_the_limit_and_keeps_the_full_bytes_with_a_preview() -> None:
    # Given more usable portrait hits than the pool has room for
    urls = [f"https://cdn.example/hit-{index}.png" for index in range(4)]
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=tuple(
                _result(f"hit-{index}", url, f"https://site-{index}.example/p")
                for index, url in enumerate(urls)
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture({url: HttpResponse(200, _png_bytes(1080, 1920), {}) for url in urls}),
    )

    # When a pool of two is collected
    collected = fetcher.collect(_QUERY, 2)

    # Then only two images are kept, each with full bytes and a downscaled preview
    assert len(collected.images) == 2
    assert collected.provider == "duckduckgo"
    for image in collected.images:
        assert len(image.preview) < len(image.content)
        with Image.open(io.BytesIO(image.preview)) as preview:
            assert max(preview.size) <= 512


def test_collect_when_nothing_decodes_then_it_reports_an_empty_pool_with_counts() -> None:
    # Given results that all fail the physical checks
    broken = "https://cdn.example/broken"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(_result("broken", broken, "https://blog.example/post/1"),),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture({broken: HttpResponse(200, b"not an image", {})}),
    )

    # When the pool is collected, then the caller learns the search answered but nothing passed
    collected = fetcher.collect(_QUERY, 6)
    assert collected.images == ()
    assert collected.results_seen == 1
    assert collected.passed_filters == 0


def test_collect_when_the_search_answers_with_nothing_then_both_counts_are_zero() -> None:
    # Given a search that returned no results at all
    search = _SearchFixture(
        response=ImageSearchResponse(provider="duckduckgo", query=_QUERY, results=())
    )
    fetcher = OpenWebBackgroundFetcher(image_search=search, http=_HttpFixture({}))

    # When the pool is collected, then the empty search is distinguishable from a filtered one
    collected = fetcher.collect(_QUERY, 6)
    assert collected.images == ()
    assert collected.results_seen == 0
    assert collected.passed_filters == 0


def test_collect_drops_stock_library_results_and_counts_them() -> None:
    # Given a pool where the first three hits are watermarked stock previews
    stock = {
        "https://cdn.shutterstock.com/photo-1.jpg": "https://www.shutterstock.com/image-photo/1",
        "https://media.gettyimages.com/photo-2.jpg": "https://www.gettyimages.co.kr/detail/2",
        "https://as2.ftcdn.net/photo-3.jpg": "https://stock.adobe.com/kr/images/3",
    }
    real = "https://cdn.example/real.png"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(
                *(
                    _result(f"stock-{index}", image_url, source_url)
                    for index, (image_url, source_url) in enumerate(stock.items())
                ),
                _result("real", real, "https://blog.example/post/9"),
            ),
        )
    )
    fetcher = OpenWebBackgroundFetcher(
        image_search=search,
        http=_HttpFixture({real: HttpResponse(200, _png_bytes(1080, 1920), {})}),
    )

    # When the judge pool is collected
    collected = fetcher.collect(_QUERY, 6)

    # Then no stock preview reached the judge, and the count is on the record
    assert [image.source_url for image in collected.images] == ["https://blog.example/post/9"]
    assert collected.filtered_stock == 3
    assert collected.results_seen == 4
    assert collected.passed_filters == 1


def test_collect_matches_stock_hosts_on_subdomains_and_the_image_url_alone() -> None:
    # Given a stock image served through a CDN under a source page that looks ordinary
    disguised = "https://image.shutterstock.com/z/photo-9.jpg"
    search = _SearchFixture(
        response=ImageSearchResponse(
            provider="duckduckgo",
            query=_QUERY,
            results=(_result("disguised", disguised, "https://blog.example/post/1"),),
        )
    )
    fetcher = OpenWebBackgroundFetcher(image_search=search, http=_HttpFixture({}))

    # When the pool is collected, then the image host alone is enough to drop it
    collected = fetcher.collect(_QUERY, 6)
    assert collected.images == ()
    assert collected.filtered_stock == 1
