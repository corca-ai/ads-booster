from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx2
from pydantic import TypeAdapter, ValidationError

from ads_booster.auth.codex import CODEX_BACKEND_URL, CodexOAuth, OAuthError
from ads_booster.providers.errors import ProviderError
from ads_booster.providers.model_catalog import available_models
from ads_booster.providers.models import (
    ProviderCacheMetrics,
    ProviderModel,
    ProviderResponseMetadata,
    ReasoningSettings,
    ResponseEnvelope,
    ResponsesRequest,
)
from ads_booster.providers.runtime_metadata import instructions_with_runtime
from ads_booster.providers.tool_schema import provider_tools
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.http import HttpClient, HttpResponse

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
HTTP_OK: Final = 200

__all__ = ["CodexResponsesClient", "FunctionCall", "ModelTurn", "ProviderError"]


@dataclass(frozen=True, slots=True)
class FunctionCall:
    call_id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str
    calls: tuple[FunctionCall, ...]
    metadata: ProviderResponseMetadata | None = None


@dataclass(frozen=True, slots=True)
class _StreamEvent:
    name: str
    payload: JsonObject


@dataclass(slots=True)  # noqa: MUTABLE_OK
class CodexResponsesClient:
    http: HttpClient
    oauth: CodexOAuth
    model: str = "gpt-5.6-luna"
    reasoning_effort: str | None = "xhigh"
    instructions: str = (
        "You are a local user-owned agent. Use tools only when they help complete the request. "
        "<trace-environment-policy>When Trace capture, image generation, or visual QA needs an "
        "installed local dependency that is inactive, inspect it, start it, verify readiness, and "
        "continue without asking the user. Do not start it for unrelated work or install missing "
        "software without the user's request.</trace-environment-policy>"
    )

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        try:
            credential = self.oauth.refresh_if_needed()
            instructions = instructions_with_runtime(self.instructions, self.model)
            request = ResponsesRequest(
                model=self.model,
                input=history,
                instructions=instructions,
                tools=provider_tools(tools),
                reasoning=(
                    ReasoningSettings(effort=self.reasoning_effort)
                    if self.reasoning_effort is not None
                    else None
                ),
            )
            payload = _JSON_OBJECT.validate_python(request.model_dump(mode="json"))
            headers = {
                "Accept": "application/json",
                "Authorization": f"{credential.token_type} {credential.access_token}",
                "Content-Type": "application/json",
                "User-Agent": "trace-agent/0.1.0",
            }
            if credential.account_id:
                headers["chatgpt-account-id"] = credential.account_id
            response = self.http.post_json(
                f"{CODEX_BACKEND_URL}/responses",
                payload,
                headers,
            )
        except OAuthError:
            raise
        except httpx2.HTTPError as error:
            msg = "provider_network"
            raise ProviderError(msg, "OpenAI provider request failed") from error
        if response.status_code != HTTP_OK:
            msg = "provider_http"
            detail = _response_detail(response)
            message = f"OpenAI provider returned HTTP {response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise ProviderError(msg, message, context_overflow=_is_context_overflow(detail))
        try:
            envelope = ResponseEnvelope.model_validate(_response_payload(response))
        except ValidationError as error:
            msg = "provider_response"
            raise ProviderError(msg, "OpenAI provider returned an invalid response") from error
        return _turn_from_response(envelope, _prefix_digest(instructions, tools))

    def available_models(self) -> tuple[ProviderModel, ...]:
        return available_models(self.http, self.oauth)


def _turn_from_response(envelope: ResponseEnvelope, prefix_digest: str) -> ModelTurn:
    text_parts: list[str] = []
    calls: list[FunctionCall] = []
    for item in envelope.output:
        if item.type == "function_call":  # noqa: IF_VARIANT_OK
            if item.call_id is None or item.name is None or item.arguments is None:
                msg = "provider_response"
                raise ProviderError(msg, "OpenAI function call was incomplete")
            try:
                arguments = _JSON_OBJECT.validate_json(item.arguments)
            except ValidationError as error:
                msg = "provider_response"
                raise ProviderError(msg, f"Tool arguments were invalid: {item.name}") from error
            calls.append(FunctionCall(item.call_id, item.name, arguments))
        elif item.type == "message":
            text_parts.extend(content.text for content in item.content if content.text)
    if not text_parts and not calls:
        output_types = ",".join(item.type for item in envelope.output) or "none"
        msg = "provider_empty"
        raise ProviderError(
            msg,
            f"OpenAI response contained no text or tool calls; output_types={output_types}",
            context_overflow=_is_context_overflow(envelope.incomplete_details),
        )
    cached_tokens = (
        envelope.usage.input_tokens_details.cached_tokens
        if envelope.usage is not None and envelope.usage.input_tokens_details is not None
        else None
    )
    metadata = ProviderResponseMetadata(
        response_id=envelope.id,
        usage=envelope.usage,
        cache=ProviderCacheMetrics(
            prefix_digest=prefix_digest,
            cached_input_tokens=cached_tokens,
            cache_hit=cached_tokens > 0 if cached_tokens is not None else None,
        ),
    )
    return ModelTurn("\n".join(text_parts), tuple(calls), metadata)


def _response_payload(response: HttpResponse) -> JsonObject:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type and b"event:" not in response.content:
        return response.json_object()
    events = _parse_sse_events(response.content)
    output_items: list[JsonValue] = []
    for stream_event in events:
        event = stream_event.payload
        event_name = stream_event.name
        event_type = event.get("type")
        if event_name == "response.output_item.done" or event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                output_items.append(item)
            continue
        if event_name != "response.completed" and event_type != "response.completed":
            continue
        completed = event.get("response")
        if isinstance(completed, dict):
            output = completed.get("output")
            if isinstance(output, list) and not output and output_items:
                completed_with_items = dict(completed)
                completed_with_items["output"] = output_items
                return completed_with_items
            return completed
    msg = "provider_response"
    raise ProviderError(msg, "OpenAI stream did not include a completed response")


def _parse_sse_events(content: bytes) -> tuple[_StreamEvent, ...]:
    try:
        stream = content.decode("utf-8")
    except UnicodeDecodeError as error:
        msg = "provider_response"
        raise ProviderError(msg, "OpenAI stream was not valid UTF-8") from error
    events: list[_StreamEvent] = []
    for block in stream.split("\n\n"):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        try:
            payload = _JSON_OBJECT.validate_json(data)
        except ValidationError:
            continue
        name = next(
            (line[6:].strip() for line in block.splitlines() if line.startswith("event:")),
            "",
        )
        events.append(_StreamEvent(name=name, payload=payload))
    return tuple(events)


def _response_detail(response: HttpResponse) -> str:
    try:
        payload = response.json_object()
    except ValidationError:
        return ""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:500]
    error = payload.get("error")
    if isinstance(error, str):
        return error[:500]
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message[:500]
    return ""


def _is_context_overflow(value: str | JsonObject | None) -> bool:
    if value is None:
        return False
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "context length exceeded",
            "context window",
            "input exceeds",
            "input is too long",
            "too many tokens",
            "request_too_large",
        )
    )


def _prefix_digest(instructions: str, tools: tuple[ToolDescriptor, ...]) -> str:
    prefix = json.dumps(
        {
            "instructions": instructions,
            "tools": [tool.model_dump(mode="json") for tool in tools],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()
