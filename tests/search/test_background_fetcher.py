from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from PIL import Image

from trace_capture.search.image.background import ImageSearchBackgroundFetcher
from trace_capture.search.image.contracts import ImageSearchResponse, ImageSearchResult
from trace_capture.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trace_capture.transport.json_types import JsonObject

_IMAGE_MODEL_CALL_MESSAGE = "background search must not call an image model"
_POST_MESSAGE = "background search must not post data"


@dataclass(frozen=True, slots=True)
class _SearchFixture:
    response: ImageSearchResponse

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        assert query == "student study vertical photo site:pexels.com"
        assert max_results == 5
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


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (720, 1280), (24, 48, 96)).save(buffer, format="PNG")
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
