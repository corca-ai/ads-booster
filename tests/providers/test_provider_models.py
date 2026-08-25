from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_capture.auth.codex import CodexOAuth
from trace_capture.auth.models import OAuthCredential
from trace_capture.auth.store import AuthStore
from trace_capture.providers.codex import CodexResponsesClient
from trace_capture.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingHttp:
    responses: list[HttpResponse]
    get_calls: list[tuple[str, Mapping[str, str]]] = field(default_factory=list)
    json_calls: list[tuple[str, JsonObject]] = field(default_factory=list)

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        self.get_calls.append((url, dict(headers)))
        return self.responses.pop(0)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = headers
        self.json_calls.append((url, payload))
        return self.responses.pop(0)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected form request"
        raise AssertionError(message)


def credential() -> OAuthCredential:
    return OAuthCredential(
        access_token="access",
        refresh_token="refresh",
        expires_at=10_000,
        account_id="account",
    )


def test_codex_provider_lists_selectable_models(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    http = RecordingHttp(
        responses=[
            HttpResponse(
                200,
                b"".join(
                    (
                        b'{"models":[{"slug":"gpt-5.5","display_name":"GPT-5.5",',
                        b'"description":"Frontier","default_reasoning_level":"medium",',
                        b'"supported_reasoning_levels":[{"effort":"low"},{"effort":"medium"},',
                        b'{"effort":"high"}]},{"slug":"hidden",',
                        b'"display_name":"Hidden","visibility":"hide"},',
                        b'{"slug":"unsupported","display_name":"Unsupported",',
                        b'"supported_in_api":false}]}',
                    )
                ),
                {},
            )
        ]
    )
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    models = client.available_models()

    assert [(model.slug, model.display_name) for model in models] == [("gpt-5.5", "GPT-5.5")]
    assert models[0].default_reasoning_level == "medium"
    assert [level.effort for level in models[0].supported_reasoning_levels] == [
        "low",
        "medium",
        "high",
    ]
    assert http.get_calls[0][0].endswith("/models?client_version=0.149.0")
    assert http.get_calls[0][1]["Authorization"] == "Bearer access"


def test_codex_provider_sends_selected_reasoning_effort(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    response = b'{"output":[{"type":"message","content":[{"type":"output_text","text":"pong"}]}]}'
    http = RecordingHttp(responses=[HttpResponse(200, response, {})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))
    client.reasoning_effort = "high"

    _ = client.respond(({"role": "user", "content": "ping"},), ())

    assert http.json_calls[0][1]["reasoning"] == {"effort": "high"}


def test_codex_provider_exposes_requested_model_to_agent(
    tmp_path: Path,
) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    response = b'{"output":[{"type":"message","content":[{"type":"output_text","text":"pong"}]}]}'
    http = RecordingHttp(responses=[HttpResponse(200, response, {})])
    client = CodexResponsesClient(
        http,
        CodexOAuth(http, store, clock=lambda: 100),
        model="gpt-5.6",
    )

    _ = client.respond(({"role": "user", "content": "지금 모델 뭐야?"},), ())

    instructions = http.json_calls[0][1]["instructions"]
    assert isinstance(instructions, str)
    metadata_start = instructions.index("<trace-agent-runtime>") + len("<trace-agent-runtime>")
    metadata_end = instructions.index("</trace-agent-runtime>")
    assert json.loads(instructions[metadata_start:metadata_end]) == {
        "requested_model": "gpt-5.6",
    }


def test_codex_provider_includes_trace_environment_policy(tmp_path: Path) -> None:
    # Given a normal trace-agent model request with its default tool instructions
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    response = b'{"output":[{"type":"message","content":[{"type":"output_text","text":"pong"}]}]}'
    http = RecordingHttp(responses=[HttpResponse(200, response, {})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    # When the provider request is assembled
    _ = client.respond(({"role": "user", "content": "Trace 이미지 만들어줘"},), ())

    # Then the model receives the structural environment policy block
    instructions = http.json_calls[0][1]["instructions"]
    assert isinstance(instructions, str)
    assert "<trace-environment-policy>" in instructions
    assert "</trace-environment-policy>" in instructions


def test_codex_provider_preserves_usage_and_cache_metadata(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    response = (
        b'{"id":"resp-1","usage":{"input_tokens":100,"output_tokens":20,'
        b'"total_tokens":120,"input_tokens_details":{"cached_tokens":80}},'
        b'"output":[{"type":"message","content":[{"type":"output_text",'
        b'"text":"pong"}]}]}'
    )
    http = RecordingHttp(responses=[HttpResponse(200, response, {})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    result = client.respond(({"role": "user", "content": "ping"},), ())

    assert result.metadata is not None
    assert result.metadata.response_id == "resp-1"
    assert result.metadata.usage is not None
    assert result.metadata.usage.total_tokens == 120
    assert result.metadata.cache.cached_input_tokens == 80
    assert result.metadata.cache.cache_hit
    assert len(result.metadata.cache.prefix_digest) == 64
