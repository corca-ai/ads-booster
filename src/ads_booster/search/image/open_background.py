"""Open-web background search for the candidate image stage.

The allowlisted `ImageSearchBackgroundFetcher` only reaches the three stock-photo hosts, so
it can never return the specific athlete, character, or idol a real user actually has on
their lock screen. This fetcher runs the model-authored query against the same open-web
image providers the agent's `image_search` tool uses and takes the first result that
downloads and decodes as a portrait-friendly image. Nothing here filters for licence:
rights review happens later, at publish time, off the recorded provenance.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

import httpx2
from PIL import Image, UnidentifiedImageError

from ads_booster.search.image.background import BackgroundSearchError, SearchedBackground
from ads_booster.search.image.contracts import ImageSearchError, ImageSearchProvider

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.search.image.contracts import ImageSearchResult
    from ads_booster.transport.http import HttpClient

_HTTP_OK: Final = 200
_MINIMUM_EDGE: Final = 800
_MAXIMUM_BYTES: Final = 20_000_000
# Ask for twenty and let the physical checks do the narrowing. Eight rows of open-web image
# search is a page of news photography — 600x400 landscape — and the 800px gate below threw
# away all of it, so the judge was handed nothing to choose between.
_MAX_RESULTS: Final = 20
_MAX_COLLECTED: Final = 6
_MAX_PER_HOST: Final = 2
# Stock libraries only ever serve watermarked previews at this size, and the judge rejects
# every one of them. Dropping them before the download keeps the pool for images a person
# could actually have saved, rather than spending judge slots on guaranteed failures.
_STOCK_HOSTS: Final = (
    "123rf.com",
    "adobestock.com",
    "alamy.com",
    "crowdpic.net",
    "depositphotos.com",
    "dreamstime.com",
    "freepik.com",
    "gettyimages.co.kr",
    "gettyimages.com",
    "istockphoto.com",
    "shutterstock.com",
    "stock.adobe.com",
    "utoimage.com",
)
_PREFERRED_MIN_ASPECT: Final = 0.8
_PREVIEW_EDGE: Final = 512
_IMAGE_ID_ALPHABET: Final = "abcdefghijklmnop"
SEARCH_FAILED_CODE: Final = "background_search_provider_failed"
_SEARCH_FAILED_MESSAGE: Final = "open web image search did not answer"
_NO_USABLE_IMAGE_CODE: Final = "background_search_no_usable_image"
_NO_USABLE_IMAGE_MESSAGE: Final = "open web image search returned no usable background image"
_WRITE_FAILED_CODE: Final = "background_artifact_write_failed"
_WRITE_FAILED_MESSAGE: Final = "searched background could not be written"
_INVALID_IMAGE_CODE: Final = "background_search_invalid_image"
_INVALID_IMAGE_MESSAGE: Final = "open web image search returned an unreadable background image"
_IMAGE_TOO_SMALL_CODE: Final = "background_search_image_too_small"
_IMAGE_TOO_SMALL_MESSAGE: Final = (
    "open web image search returned a background image below the minimum resolution"
)
_SEARCH_HEADERS: Final = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": "trace-agent/0.2.1",
}


@dataclass(frozen=True, slots=True)
class CollectedBackground:
    """One downloaded image that passed the physical checks, kept for the judge to look at.

    `content` is the normalised PNG that would be written if this image wins; `preview` is
    the same image downscaled to something a provider call can carry.
    """

    image_id: str
    content: bytes
    preview: bytes
    image_url: str
    source_url: str


@dataclass(frozen=True, slots=True)
class _Usable:
    """One downloaded image that passed the physical checks, before the pool is ordered."""

    content: bytes
    preview: bytes
    image_url: str
    source_url: str


@dataclass(frozen=True, slots=True)
class CollectedBackgrounds:
    """Everything one open-web search produced, with the query and provider that answered.

    `images` can be empty, and the two counts are what tell an empty pool apart: a search
    that returned nothing at all is a different problem from a search whose every result
    failed the physical checks, and only the caller can decide what to do about each.
    """

    query: str
    provider: str
    images: tuple[CollectedBackground, ...]
    results_seen: int = 0
    passed_filters: int = 0
    filtered_stock: int = 0


@dataclass(frozen=True, slots=True)
class OpenWebBackgroundFetcher:
    """Fetches one candidate background from open-web image search, first usable hit wins."""

    image_search: ImageSearchProvider
    http: HttpClient
    max_results: int = _MAX_RESULTS
    minimum_edge: int = _MINIMUM_EDGE
    maximum_bytes: int = _MAXIMUM_BYTES
    maximum_per_host: int = _MAX_PER_HOST
    minimum_aspect: float = _PREFERRED_MIN_ASPECT

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        try:
            response = self.image_search.search(query, self.max_results)
        except ImageSearchError as error:
            raise BackgroundSearchError(SEARCH_FAILED_CODE, _SEARCH_FAILED_MESSAGE) from error
        for result in response.results:
            content = self._download(result.image_url)
            if content is None:
                continue
            try:
                self._store(content, destination)
            except BackgroundSearchError:
                continue
            except OSError as error:
                raise BackgroundSearchError(_WRITE_FAILED_CODE, _WRITE_FAILED_MESSAGE) from error
            return SearchedBackground(
                path=destination,
                sha256=sha256(destination.read_bytes()).hexdigest(),
                query=response.query,
                provider=response.provider,
                image_url=result.image_url,
                source_url=result.source_url,
            )
        raise BackgroundSearchError(_NO_USABLE_IMAGE_CODE, _NO_USABLE_IMAGE_MESSAGE)

    def collect(self, query: str, limit: int = _MAX_COLLECTED) -> CollectedBackgrounds:
        """Gather up to `limit` images that pass the physical checks, judge order untouched.

        Nothing here decides which background is good: this step only proves that an image
        exists, downloads, decodes, and is large enough to use. An empty pool is returned
        rather than raised, because the caller runs a query ladder over this and needs the
        counts to choose its next move. Three cheap retrieval filters
        do run first, because a judge slot spent on an image the composition can never use
        is a slot the good candidate does not get: portrait and near-square images take the
        slots ahead of landscape ones, no single source may supply more than
        `maximum_per_host` of the pool, and stock-library results are dropped outright.
        The AI judge picks the winner from what is left.
        """
        try:
            response = self.image_search.search(query, self.max_results)
        except ImageSearchError as error:
            raise BackgroundSearchError(SEARCH_FAILED_CODE, _SEARCH_FAILED_MESSAGE) from error
        preferred, landscape, stock = self._usable(response.results, limit)
        usable = [*preferred, *landscape]
        pool = usable[:limit]
        return CollectedBackgrounds(
            query=response.query,
            provider=response.provider,
            images=tuple(
                CollectedBackground(
                    image_id=f"img-{_IMAGE_ID_ALPHABET[index]}",
                    content=entry.content,
                    preview=entry.preview,
                    image_url=entry.image_url,
                    source_url=entry.source_url,
                )
                for index, entry in enumerate(pool)
            ),
            results_seen=len(response.results),
            passed_filters=len(usable),
            filtered_stock=stock,
        )

    def _usable(
        self,
        results: tuple[ImageSearchResult, ...],
        limit: int,
    ) -> tuple[list[_Usable], list[_Usable], int]:
        """Download the results worth downloading, split into portrait-ish and landscape.

        The per-host cap is applied before the download so six near-duplicates from one
        site cost nothing, and the walk stops as soon as enough portrait images exist to
        fill the pool on their own.
        """
        preferred: list[_Usable] = []
        landscape: list[_Usable] = []
        per_host: Counter[str] = Counter()
        stock = 0
        for result in results:
            if len(preferred) >= limit:
                break
            if _is_stock(result.source_url) or _is_stock(result.image_url):
                stock += 1
                continue
            host = _host(result.source_url)
            if per_host[host] >= self.maximum_per_host:
                continue
            content = self._download(result.image_url)
            if content is None:
                continue
            normalized = self._normalize(content)
            if normalized is None:
                continue
            png, preview, portrait = normalized
            per_host[host] += 1
            usable = _Usable(
                content=png,
                preview=preview,
                image_url=result.image_url,
                source_url=result.source_url,
            )
            (preferred if portrait else landscape).append(usable)
        return preferred, landscape, stock

    def _normalize(self, content: bytes) -> tuple[bytes, bytes, bool] | None:
        """Return the storable PNG, its judging preview, and whether it is portrait-ish.

        `None` means the bytes failed a physical check and the image is not a candidate at
        all; a `False` orientation flag only sends the image to the back of the queue.
        """
        try:
            with Image.open(io.BytesIO(content)) as image:
                _ = image.load()
                width, height = image.size
                normalized = image.convert("RGB")
                if min(width, height) < self.minimum_edge:
                    return None
                preview = normalized.copy()
                preview.thumbnail((_PREVIEW_EDGE, _PREVIEW_EDGE))
                portrait = height / width >= self.minimum_aspect
                return _encode(normalized, "PNG"), _encode(preview, "JPEG"), portrait
        except OSError, UnidentifiedImageError, SyntaxError:
            return None

    def _download(self, image_url: str) -> bytes | None:
        """Return the image bytes, or `None` for any result that is not worth decoding."""
        try:
            response = self.http.get(image_url, _SEARCH_HEADERS)
        except httpx2.HTTPError:
            return None
        if response.status_code != _HTTP_OK:
            return None
        content = response.content
        if not content or len(content) > self.maximum_bytes:
            return None
        return content

    def _store(self, content: bytes, destination: Path) -> None:
        """Verify the bytes really decode at a usable size, then write them as PNG."""
        try:
            with Image.open(io.BytesIO(content)) as image:
                _ = image.load()
                width, height = image.size
                normalized = image.convert("RGB")
        except (OSError, UnidentifiedImageError, SyntaxError) as error:
            raise BackgroundSearchError(_INVALID_IMAGE_CODE, _INVALID_IMAGE_MESSAGE) from error
        if min(width, height) < self.minimum_edge:
            raise BackgroundSearchError(_IMAGE_TOO_SMALL_CODE, _IMAGE_TOO_SMALL_MESSAGE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(destination, format="PNG")


def _encode(image: Image.Image, image_format: str) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def _host(source_url: str) -> str:
    """The source page's host, so two images from one site are recognisably one source."""
    return (urlsplit(source_url).hostname or source_url).removeprefix("www.")


def _is_stock(url: str) -> bool:
    """True for a stock-library URL, matched on the host so subdomains count too."""
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return any(host == stock or host.endswith(f".{stock}") for stock in _STOCK_HOSTS)
