from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_capture.agent.session import AgentSession
from trace_capture.providers.codex import FunctionCall, ModelTurn
from trace_capture.search.image.contracts import (
    ImageSearchResponse,
    ImageSearchResult,
)
from trace_capture.search.image.providers import UnavailableImageSearchProvider
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.image_search import ImageSearchTool
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import default_registry

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class FakeImageSearchProvider:
    response: ImageSearchResponse
    calls: list[tuple[str, int]] = field(default_factory=list)

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        self.calls.append((query, max_results))
        return self.response


@dataclass(frozen=True, slots=True)
class ImageSearchModel:
    turns: list[ModelTurn]

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        return self.turns.pop(0)


def test_default_registry_exposes_image_search() -> None:
    # Given the production agent registry
    registry = default_registry()

    # When the model-visible tool descriptors are built
    names = {descriptor.name for descriptor in registry.descriptors()}

    # Then image search is a separate model capability
    assert "image_search" in names


def test_image_search_descriptor_exposes_image_fields_and_bounds() -> None:
    # Given the model-visible image search descriptor
    descriptor = ImageSearchTool().descriptor()

    # When the descriptor schema is inspected
    properties = descriptor.parameters.get("properties")
    assert isinstance(properties, dict)

    # Then image search has a bounded read-only contract
    assert descriptor.name == "image_search"
    assert properties.get("max_results") == {
        "type": "integer",
        "default": 5,
        "minimum": 1,
        "maximum": 10,
    }
    assert "image URL" in descriptor.description


def test_image_search_tool_returns_normalized_image_provenance(tmp_path: Path) -> None:
    # Given an image provider result with image and source URLs
    response = ImageSearchResponse(
        provider="fake",
        query="Trace lock screen",
        results=(
            ImageSearchResult(
                title="Trace image",
                image_url="https://cdn.example/image.jpg",
                thumbnail_url="https://cdn.example/thumb.jpg",
                source_url="https://example.com/post",
                width=1200,
                height=800,
                source="Example",
            ),
        ),
    )
    provider = FakeImageSearchProvider(response)
    context = ToolContext(tmp_path, DenyApproval(), (), image_search=provider)

    # When the model calls image_search
    result = ImageSearchTool().execute({"query": " Trace lock screen ", "max_results": 1}, context)

    # Then image URLs, source provenance, and dimensions are returned
    parsed = ImageSearchResponse.model_validate_json(result.output)
    assert result.ok
    assert parsed.provider == "fake"
    assert parsed.query == "Trace lock screen"
    assert parsed.results[0].image_url == "https://cdn.example/image.jpg"
    assert parsed.results[0].source_url == "https://example.com/post"
    assert parsed.results[0].width == 1200
    assert provider.calls == [("Trace lock screen", 1)]


def test_image_search_tool_fails_closed_for_invalid_or_unconfigured_calls(tmp_path: Path) -> None:
    # Given an agent context without an image provider
    context = ToolContext(tmp_path, DenyApproval(), ())

    # When the model sends an empty image query
    invalid = ImageSearchTool().execute({"query": "   "}, context)

    # Then it receives a typed validation failure
    assert invalid.error_code == "invalid_arguments"

    # When the model sends a valid query without an image provider
    unavailable = ImageSearchTool().execute({"query": "Trace"}, context)

    # Then image search remains explicitly unavailable
    assert unavailable.error_code == "image_search_unavailable"


def test_image_search_tool_maps_provider_failure(tmp_path: Path) -> None:
    # Given an image provider that reports an expected upstream failure
    provider = UnavailableImageSearchProvider("image provider is disabled")
    context = ToolContext(tmp_path, DenyApproval(), (), image_search=provider)

    # When the model calls image_search
    result = ImageSearchTool().execute({"query": "Trace"}, context)

    # Then the typed provider error reaches the model
    assert result.error_code == "image_search_unavailable"
    assert result.output == "image provider is disabled"


def test_agent_session_executes_image_search_then_returns_answer(tmp_path: Path) -> None:
    # Given a model turn that calls image_search before answering
    response = ImageSearchResponse(
        provider="fake",
        query="Trace",
        results=(
            ImageSearchResult(
                title="Trace",
                image_url="https://cdn.example/image.jpg",
                thumbnail_url="https://cdn.example/thumb.jpg",
                source_url="https://example.com",
            ),
        ),
    )
    provider = FakeImageSearchProvider(response)
    model = ImageSearchModel(
        [
            ModelTurn("", (FunctionCall("image-1", "image_search", {"query": "Trace"}),)),
            ModelTurn("image answer", ()),
        ]
    )
    session = AgentSession(
        model,
        default_registry(),
        ToolContext(tmp_path, DenyApproval(), (), image_search=provider),
    )

    # When the AgentSession tool loop handles the request
    answer = session.ask("Find an image for Trace")

    # Then the model receives image provenance and returns its answer
    assert answer == "image answer"
    assert provider.calls == [("Trace", 5)]
    output = next(
        item["output"] for item in session.history if item.get("type") == "function_call_output"
    )
    assert "https://cdn.example/image.jpg" in str(output)
