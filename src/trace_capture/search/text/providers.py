from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import httpx2
from pydantic import TypeAdapter, ValidationError

from trace_capture.search.text.contracts import (
    SearchResponse,
    SearchResult,
    WebSearchError,
    WebSearchProvider,
)
from trace_capture.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from trace_capture.transport.http import HttpClient

_JSON_ROWS: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])
_BRAVE_ENDPOINT: Final = "https://api.search.brave.com/res/v1/web/search"
_HTTP_OK: Final = 200
_MAX_RESULTS: Final = 10
_CODE_INVALID_ARGUMENTS: Final = "invalid_arguments"
_CODE_HTTP: Final = "web_search_http"
_CODE_NETWORK: Final = "web_search_network"
_CODE_PROVIDER: Final = "web_search_provider"
_CODE_RESPONSE: Final = "web_search_response"
_CODE_TIMEOUT: Final = "web_search_timeout"
_CODE_UNAVAILABLE: Final = "web_search_unavailable"
_MESSAGE_DDGS_MISSING: Final = "ddgs is not installed or not on PATH"
_MESSAGE_DDGS_TIMEOUT: Final = "DuckDuckGo search timed out"
_MESSAGE_DDGS_START: Final = "DuckDuckGo search failed to start"
_MESSAGE_DDGS_INVALID: Final = "DuckDuckGo search returned invalid data"
_MESSAGE_BRAVE_NETWORK: Final = "Brave search request failed"
_MESSAGE_BRAVE_NO_RESULTS: Final = "Brave search response had no web results"
_MESSAGE_BRAVE_INVALID: Final = "Brave search response was invalid"
_MESSAGE_COUNT: Final = "max_results must be between 1 and 10"


@dataclass(frozen=True, slots=True)
class DdgsSearchProvider:
    timeout_seconds: float = 30.0

    def search(self, query: str, max_results: int) -> SearchResponse:
        count = _validated_count(max_results)
        command = shutil.which("ddgs")
        if command is None:
            raise WebSearchError(_CODE_UNAVAILABLE, _MESSAGE_DDGS_MISSING)
        with tempfile.TemporaryDirectory(prefix="trace-agent-ddgs-") as temp_dir:
            output_path = Path(temp_dir) / "results.json"
            argv = (
                command,
                "text",
                "--query",
                query,
                "--max_results",
                str(count),
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
                raise WebSearchError(_CODE_TIMEOUT, _MESSAGE_DDGS_TIMEOUT) from error
            except OSError as error:
                raise WebSearchError(_CODE_PROVIDER, _MESSAGE_DDGS_START) from error
            if completed.returncode != 0:
                raise WebSearchError(_CODE_PROVIDER, _MESSAGE_DDGS_INVALID)
            try:
                rows = _JSON_ROWS.validate_json(output_path.read_bytes())
            except (OSError, ValidationError) as error:
                raise WebSearchError(_CODE_RESPONSE, _MESSAGE_DDGS_INVALID) from error
        return _response_from_rows(_ResultShape("duckduckgo", query, "href", "body"), rows, count)


@dataclass(frozen=True, slots=True)
class BraveSearchProvider:
    http: HttpClient
    api_key: str
    endpoint: str = _BRAVE_ENDPOINT

    def search(self, query: str, max_results: int) -> SearchResponse:
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
            raise WebSearchError(_CODE_NETWORK, _MESSAGE_BRAVE_NETWORK) from error
        if response.status_code != _HTTP_OK:
            raise WebSearchError(
                _CODE_HTTP,
                f"Brave search returned HTTP {response.status_code}",
            )
        try:
            payload = response.json_object()
            web = payload.get("web")
            if not isinstance(web, dict):
                raise WebSearchError(_CODE_RESPONSE, _MESSAGE_BRAVE_NO_RESULTS)
            rows = _JSON_ROWS.validate_python(web.get("results", []))
        except ValidationError as error:
            raise WebSearchError(_CODE_RESPONSE, _MESSAGE_BRAVE_INVALID) from error
        return _response_from_rows(_ResultShape("brave", query, "url", "description"), rows, count)


@dataclass(frozen=True, slots=True)
class UnavailableSearchProvider:
    message: str

    def search(self, query: str, max_results: int) -> SearchResponse:
        _ = (query, max_results)
        raise WebSearchError(_CODE_UNAVAILABLE, self.message)


def create_web_search_provider(
    http: HttpClient,
    provider_name: str,
    timeout_seconds: float,
) -> WebSearchProvider:
    selected = provider_name.strip().lower()
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if selected in {"", "auto"}:
        if brave_key:
            return BraveSearchProvider(http=http, api_key=brave_key)
        return DdgsSearchProvider(timeout_seconds=timeout_seconds)
    if selected in {"ddgs", "duckduckgo"}:
        return DdgsSearchProvider(timeout_seconds=timeout_seconds)
    if selected == "brave":
        if brave_key is None:
            return UnavailableSearchProvider("BRAVE_SEARCH_API_KEY is not configured")
        return BraveSearchProvider(http=http, api_key=brave_key)
    return UnavailableSearchProvider(f"unknown web search provider: {provider_name}")


def _validated_count(max_results: int) -> int:
    if not 1 <= max_results <= _MAX_RESULTS:
        raise WebSearchError(_CODE_INVALID_ARGUMENTS, _MESSAGE_COUNT)
    return max_results


@dataclass(frozen=True, slots=True)
class _ResultShape:
    provider: str
    query: str
    url_key: str
    snippet_key: str


def _response_from_rows(
    shape: _ResultShape,
    rows: list[JsonObject],
    max_results: int,
) -> SearchResponse:
    results: list[SearchResult] = []
    for row in rows:
        title = _string_value(row.get("title"))
        url = _string_value(row.get(shape.url_key))
        if title is None or url is None:
            continue
        snippet = _string_value(row.get(shape.snippet_key)) or ""
        try:
            result = SearchResult(title=title, url=url, snippet=snippet)
        except ValidationError:
            continue
        results.append(result)
        if len(results) == max_results:
            break
    return SearchResponse(provider=shape.provider, query=shape.query, results=tuple(results))


def _string_value(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
