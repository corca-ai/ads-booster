from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Final, override
from urllib.parse import urlsplit

import httpx2
from PIL import Image, UnidentifiedImageError

from ads_booster.search.image.contracts import ImageSearchError, ImageSearchProvider

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonValue

_HTTP_OK: Final = 200
_MINIMUM_EDGE: Final = 640
_WRITE_FAILED_CODE: Final = "background_artifact_write_failed"
_WRITE_FAILED_MESSAGE: Final = "searched background could not be written"
_NO_USABLE_IMAGE_CODE: Final = "background_search_no_usable_image"
_NO_USABLE_IMAGE_MESSAGE: Final = "image search returned no usable approved background image"
_INVALID_IMAGE_CODE: Final = "background_search_invalid_image"
_INVALID_IMAGE_MESSAGE: Final = "image search returned an unreadable background image"
_IMAGE_TOO_SMALL_CODE: Final = "background_search_image_too_small"
_IMAGE_TOO_SMALL_MESSAGE: Final = (
    "image search returned a background image below the minimum resolution"
)
_APPROVED_SOURCE_HOSTS: Final = frozenset(
    {
        "unsplash.com",
        "www.unsplash.com",
        "pexels.com",
        "www.pexels.com",
        "pixabay.com",
        "www.pixabay.com",
    }
)
_APPROVED_SEARCH_DOMAINS: Final = ("pexels.com", "unsplash.com", "pixabay.com")
_SEARCH_HEADERS: Final = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": "trace-agent/0.2.1",
}


@dataclass(frozen=True, slots=True)
class BackgroundSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SearchedBackground:
    path: Path
    sha256: str
    query: str
    provider: str
    image_url: str
    source_url: str
    # Extra facts the fetcher that produced this background wants on the artifact record,
    # merged into the provenance file under their own keys. The stock-allowlist fetcher has
    # none; the judged open-web fetcher records why this image beat the ones it was shown.
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def write_provenance(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, JsonValue] = {
            "schema_version": "trace.background-search.v1",
            "query": self.query,
            "provider": self.provider,
            "image_url": self.image_url,
            "source_url": self.source_url,
            "artifact_sha256": self.sha256,
            **self.details,
        }
        _ = destination.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class ImageSearchBackgroundFetcher:
    image_search: ImageSearchProvider
    http: HttpClient
    max_results: int = 5

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        for domain in _APPROVED_SEARCH_DOMAINS:
            try:
                response = self.image_search.search(f"{query} site:{domain}", self.max_results)
            except ImageSearchError:
                continue
            for result in response.results:
                if not _is_approved_source(result.source_url):
                    continue
                try:
                    http_response = self.http.get(result.image_url, _SEARCH_HEADERS)
                except httpx2.HTTPError:
                    continue
                if http_response.status_code != _HTTP_OK:
                    continue
                try:
                    _write_png(http_response.content, destination)
                except BackgroundSearchError:
                    continue
                except OSError as error:
                    raise BackgroundSearchError(
                        _WRITE_FAILED_CODE,
                        _WRITE_FAILED_MESSAGE,
                    ) from error
                return SearchedBackground(
                    path=destination,
                    sha256=sha256(destination.read_bytes()).hexdigest(),
                    query=response.query,
                    provider=response.provider,
                    image_url=result.image_url,
                    source_url=result.source_url,
                )
        raise BackgroundSearchError(
            _NO_USABLE_IMAGE_CODE,
            _NO_USABLE_IMAGE_MESSAGE,
        )


def _write_png(content: bytes, destination: Path) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            _ = image.load()
            width, height = image.size
            normalized = image.convert("RGB")
    except (OSError, UnidentifiedImageError, SyntaxError) as error:
        raise BackgroundSearchError(
            _INVALID_IMAGE_CODE,
            _INVALID_IMAGE_MESSAGE,
        ) from error
    if min(width, height) < _MINIMUM_EDGE:
        raise BackgroundSearchError(
            _IMAGE_TOO_SMALL_CODE,
            _IMAGE_TOO_SMALL_MESSAGE,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(destination, format="PNG")


def _is_approved_source(source_url: str) -> bool:
    host = urlsplit(source_url).hostname
    return host in _APPROVED_SOURCE_HOSTS
