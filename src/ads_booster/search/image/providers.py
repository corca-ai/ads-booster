from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import httpx2
from pydantic import TypeAdapter, ValidationError

from ads_booster.search.image.contracts import (
    MAX_IMAGE_SEARCH_RESULTS,
    ImageSearchError,
    ImageSearchProvider,
    ImageSearchResponse,
    ImageSearchResult,
)
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from ads_booster.transport.http import HttpClient

_JSON_ROWS: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])
_BRAVE_ENDPOINT: Final = "https://api.search.brave.com/res/v1/images/search"
_HTTP_OK: Final = 200
# The response contract sets the ceiling; reading it from there keeps the two from drifting
# apart again. The resolution gate downstream is the real limit: a page of news photography
# is all 600x400, so a short page routinely left nothing for the judge to look at. Asking for
# more costs one request either way.
_MAX_RESULTS: Final = MAX_IMAGE_SEARCH_RESULTS
# Open-web image search returns article photography by default, which is landscape and small.
# A lock screen needs the opposite. These are asked for, but do not count on them: measured
# against live searches, --layout Tall changes nothing at all - "고양이 배경화면 고화질"
# returned 1 portrait image in 20 with the flag and 2 in 20 without it, and "쿠로미 배경화면"
# returned the same 3 either way. Orientation has to come from the query wording and from
# ranking a wide pool downstream, not from the provider.
_DDGS_SIZE: Final = "Wallpaper"
_DDGS_LAYOUT: Final = "Tall"
_DDGS_TYPE: Final = "photo"
_DDGS_SAFESEARCH: Final = "moderate"
_CODE_INVALID_ARGUMENTS: Final = "invalid_arguments"
_CODE_HTTP: Final = "image_search_http"
_CODE_NETWORK: Final = "image_search_network"
_CODE_PROVIDER: Final = "image_search_provider"
_CODE_RESPONSE: Final = "image_search_response"
_CODE_TIMEOUT: Final = "image_search_timeout"
_CODE_UNAVAILABLE: Final = "image_search_unavailable"
_MESSAGE_MISSING: Final = "ddgs is not installed or not on PATH"
_MESSAGE_TIMEOUT: Final = "DDGS image search timed out"
_MESSAGE_START: Final = "DDGS image search failed to start"
_MESSAGE_INVALID: Final = "DDGS image search returned invalid data"
_MESSAGE_BRAVE_NETWORK: Final = "Brave image search request failed"
_MESSAGE_BRAVE_INVALID: Final = "Brave image search response was invalid"


@dataclass(frozen=True, slots=True)
class DdgsImageSearchProvider:
    timeout_seconds: float = 30.0

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        count = _validated_count(max_results)
        command = _ddgs_command()
        if command is None:
            raise ImageSearchError(_CODE_UNAVAILABLE, _MESSAGE_MISSING)
        with tempfile.TemporaryDirectory(prefix="trace-agent-ddgs-images-") as temp_dir:
            output_path = Path(temp_dir) / "results.json"
            argv = (
                command,
                "images",
                "--query",
                query,
                "--max_results",
                str(count),
                "--size",
                _DDGS_SIZE,
                "--layout",
                _DDGS_LAYOUT,
                "--type_image",
                _DDGS_TYPE,
                "--safesearch",
                _DDGS_SAFESEARCH,
                "--output",
                str(output_path),
                "--no-color",
            )
            try:
                completed = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise ImageSearchError(_CODE_TIMEOUT, _MESSAGE_TIMEOUT) from error
            except OSError as error:
                raise ImageSearchError(_CODE_PROVIDER, _MESSAGE_START) from error
            if completed.returncode != 0:
                raise ImageSearchError(_CODE_PROVIDER, _MESSAGE_INVALID)
            try:
                rows = _JSON_ROWS.validate_json(output_path.read_bytes())
            except (OSError, ValidationError) as error:
                raise ImageSearchError(_CODE_RESPONSE, _MESSAGE_INVALID) from error
        return _response_from_rows("duckduckgo", query, rows, count)


@dataclass(frozen=True, slots=True)
class BraveImageSearchProvider:
    http: HttpClient
    api_key: str
    endpoint: str = _BRAVE_ENDPOINT

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        count = _validated_count(max_results)
        url = f"{self.endpoint}?{urlencode({'q': query, 'count': str(count)})}"
        try:
            response = self.http.get(
                url,
                {
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.api_key,
                    "User-Agent": "trace-agent/0.1.0",
                },
            )
        except httpx2.HTTPError as error:
            raise ImageSearchError(_CODE_NETWORK, _MESSAGE_BRAVE_NETWORK) from error
        if response.status_code != _HTTP_OK:
            raise ImageSearchError(
                _CODE_HTTP,
                f"Brave image search returned HTTP {response.status_code}",
            )
        try:
            rows = _JSON_ROWS.validate_python(response.json_object().get("results", []))
        except ValidationError as error:
            raise ImageSearchError(_CODE_RESPONSE, _MESSAGE_BRAVE_INVALID) from error
        return _response_from_rows("brave", query, rows, count)


@dataclass(frozen=True, slots=True)
class UnavailableImageSearchProvider:
    message: str

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        _ = (query, max_results)
        raise ImageSearchError(_CODE_UNAVAILABLE, self.message)


def create_image_search_provider(
    http: HttpClient,
    provider_name: str,
    timeout_seconds: float,
) -> ImageSearchProvider:
    selected = provider_name.strip().lower()
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if selected in {"", "auto"}:
        if brave_key:
            return BraveImageSearchProvider(http=http, api_key=brave_key)
        return DdgsImageSearchProvider(timeout_seconds=timeout_seconds)
    if selected in {"ddgs", "duckduckgo"}:
        return DdgsImageSearchProvider(timeout_seconds=timeout_seconds)
    if selected == "brave":
        if brave_key is None:
            return UnavailableImageSearchProvider("BRAVE_SEARCH_API_KEY is not configured")
        return BraveImageSearchProvider(http=http, api_key=brave_key)
    return UnavailableImageSearchProvider(f"unknown image search provider: {provider_name}")


def _validated_count(max_results: int) -> int:
    if not 1 <= max_results <= _MAX_RESULTS:
        message = f"max_results must be between 1 and {_MAX_RESULTS}"
        raise ImageSearchError(_CODE_INVALID_ARGUMENTS, message)
    return max_results


def _ddgs_command() -> str | None:
    command = shutil.which("ddgs")
    if command is not None:
        return command
    sibling = Path(sys.executable).with_name("ddgs")
    return str(sibling) if sibling.is_file() else None


def _response_from_rows(
    provider: str,
    query: str,
    rows: list[JsonObject],
    max_results: int,
) -> ImageSearchResponse:
    results: list[ImageSearchResult] = []
    for row in rows:
        result = _result_from_row(row)
        if result is None:
            continue
        results.append(result)
        if len(results) == max_results:
            break
    return ImageSearchResponse(provider=provider, query=query, results=tuple(results))


def _result_from_row(row: JsonObject) -> ImageSearchResult | None:
    title = _string_value(row.get("title"))
    image_url = (
        _string_value(row.get("image"))
        or _nested_string(row.get("image"), "url")
        or _nested_string(row.get("properties"), "url")
    )
    thumbnail_url = _string_value(row.get("thumbnail")) or _nested_string(
        row.get("thumbnail"), "src"
    )
    source_url = _string_value(row.get("url"))
    if title is None or image_url is None or thumbnail_url is None or source_url is None:
        return None
    try:
        return ImageSearchResult(
            title=title,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            source_url=source_url,
            width=_integer_value(row.get("width"))
            or _nested_integer(row.get("properties"), "width"),
            height=_integer_value(row.get("height"))
            or _nested_integer(row.get("properties"), "height"),
            source=_string_value(row.get("source")) or "",
        )
    except ValidationError:
        return None


def _nested_string(value: JsonValue | None, key: str) -> str | None:
    if not isinstance(value, dict):
        return None
    return _string_value(value.get(key))


def _nested_integer(value: JsonValue | None, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    return _integer_value(value.get(key))


def _string_value(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _integer_value(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
