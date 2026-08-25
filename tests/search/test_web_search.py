from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest

from trace_capture.agent.session import AgentSession
from trace_capture.providers.codex import FunctionCall, ModelTurn
from trace_capture.search.text.contracts import (
    SearchResponse,
    SearchResult,
    WebSearchError,
)
from trace_capture.search.text.providers import (
    BraveSearchProvider,
    UnavailableSearchProvider,
    create_web_search_provider,
)
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import default_registry
from trace_capture.tools.text_search import WebSearchTool
from trace_capture.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class FakeSearchProvider:
    response: SearchResponse
    calls: list[tuple[str, int]] = field(default_factory=list)

    def search(self, query: str, max_results: int) -> SearchResponse:
        self.calls.append((query, max_results))
        return self.response


@dataclass(frozen=True, slots=True)
class SearchModel:
    turns: list[ModelTurn]

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        return self.turns.pop(0)


@dataclass(slots=True)
class RecordingHttp:
    response: HttpResponse
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        self.url = url
        self.headers = dict(headers)
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


def test_default_registry_exposes_web_search() -> None:
    # Given the production agent registry
    registry = default_registry()

    # When the model-visible tool descriptors are built
    names = {descriptor.name for descriptor in registry.descriptors()}

    # Then web search is an explicit capability separate from browser automation
    assert "web_search" in names


def test_web_search_descriptor_is_read_only_and_bounded() -> None:
    # Given the model-visible web search descriptor
    descriptor = WebSearchTool().descriptor()

    # When the descriptor schema is inspected
    properties = descriptor.parameters.get("properties")
    assert isinstance(properties, dict)

    # Then the search surface has a bounded query and result count without mutation actions
    assert descriptor.name == "web_search"
    assert properties.get("max_results") == {
        "type": "integer",
        "default": 5,
        "minimum": 1,
        "maximum": 10,
    }
    assert "approval" not in descriptor.parameters


def test_web_search_tool_returns_normalized_source_provenance(tmp_path: Path) -> None:
    # Given a provider result with an external source URL
    response = SearchResponse(
        provider="fake",
        query="Trace agent",
        results=(
            SearchResult(
                title="Trace agent docs",
                url="https://example.com/trace",
                snippet="A source snippet",
            ),
        ),
    )
    provider = FakeSearchProvider(response)
    context = ToolContext(tmp_path, DenyApproval(), (), web_search=provider)

    # When the model calls web_search
    result = WebSearchTool().execute({"query": " Trace agent ", "max_results": 1}, context)

    # Then the result is structured and the normalized query reaches the provider
    parsed = SearchResponse.model_validate_json(result.output)
    assert result.ok
    assert parsed.provider == "fake"
    assert parsed.query == "Trace agent"
    assert parsed.results[0].url == "https://example.com/trace"
    assert provider.calls == [("Trace agent", 1)]


def test_web_search_tool_fails_closed_for_invalid_or_unconfigured_calls(tmp_path: Path) -> None:
    # Given an agent context without a configured provider
    context = ToolContext(tmp_path, DenyApproval(), ())

    # When the model sends invalid input
    invalid = WebSearchTool().execute({"query": "   "}, context)

    # Then it receives a typed boundary failure without network access
    assert invalid.error_code == "invalid_arguments"

    # When the model sends valid input without a provider
    unavailable = WebSearchTool().execute({"query": "Trace"}, context)

    # Then the capability remains explicitly unavailable
    assert unavailable.error_code == "web_search_unavailable"


def test_web_search_tool_maps_provider_failure(tmp_path: Path) -> None:
    # Given a provider that reports an expected upstream failure
    provider = UnavailableSearchProvider("provider is disabled")
    context = ToolContext(tmp_path, DenyApproval(), (), web_search=provider)

    # When the model calls the tool
    result = WebSearchTool().execute({"query": "Trace"}, context)

    # Then the typed provider code is preserved for the model
    assert result.error_code == "web_search_unavailable"
    assert result.output == "provider is disabled"


def test_agent_session_executes_web_search_then_returns_answer(tmp_path: Path) -> None:
    # Given a model turn that calls web_search before answering
    response = SearchResponse(
        provider="fake",
        query="Trace",
        results=(SearchResult(title="Trace", url="https://example.com", snippet="source"),),
    )
    provider = FakeSearchProvider(response)
    model = SearchModel(
        [
            ModelTurn("", (FunctionCall("search-1", "web_search", {"query": "Trace"}),)),
            ModelTurn("answer", ()),
        ]
    )
    session = AgentSession(
        model,
        default_registry(),
        ToolContext(tmp_path, DenyApproval(), (), web_search=provider),
    )

    # When the real AgentSession tool loop handles the request
    answer = session.ask("Search for Trace")

    # Then the model receives a source-bearing tool output and returns its answer
    assert answer == "answer"
    assert provider.calls == [("Trace", 5)]
    output = next(
        item["output"] for item in session.history if item.get("type") == "function_call_output"
    )
    assert "https://example.com" in str(output)


def test_brave_provider_normalizes_results_and_limits_count() -> None:
    # Given a Brave-shaped response with more results than requested
    http = RecordingHttp(
        HttpResponse(
            200,
            (
                b'{"web":{"results":['
                b'{"title":"One","url":"https://one.example","description":"first"},'
                b'{"title":"Two","url":"https://two.example","description":"second"}'
                b"]}}"
            ),
            {},
        )
    )
    provider = BraveSearchProvider(http=http, api_key="secret")

    # When the provider searches for one result
    response = provider.search("Trace agent", 1)

    # Then it sends the query to Brave and returns only the requested normalized source
    assert parse_qs(urlsplit(http.url).query) == {"q": ["Trace agent"], "count": ["1"]}
    assert http.headers["X-Subscription-Token"] == "secret"
    assert len(response.results) == 1
    assert response.results[0].title == "One"
    assert response.results[0].url == "https://one.example"


def test_web_search_provider_selection_fails_closed_without_brave_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given explicit Brave selection without an API key
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    provider = create_web_search_provider(RecordingHttp(HttpResponse(200, b"{}", {})), "brave", 30)

    # When the provider is called
    with pytest.raises(WebSearchError, match="BRAVE_SEARCH_API_KEY"):
        _ = provider.search("Trace", 5)
