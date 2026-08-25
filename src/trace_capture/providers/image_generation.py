from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

import httpx2
from PIL import Image, UnidentifiedImageError
from pydantic import TypeAdapter, ValidationError

from trace_capture.auth.codex import CODEX_BACKEND_URL, OAuthError
from trace_capture.providers.errors import ProviderError
from trace_capture.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.auth.models import OAuthCredential
    from trace_capture.transport.http import HttpClient, HttpResponse

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
HTTP_OK: Final = 200
CODEX_IMAGE_URL: Final = f"{CODEX_BACKEND_URL}/responses"
IMAGE_PROVIDER_HTTP: Final = "image_provider_http"
IMAGE_PROVIDER_RESPONSE: Final = "image_provider_response"
IMAGE_PROVIDER_IMAGE_INVALID: Final = "image_provider_image_invalid"
IMAGE_ARTIFACT_WRITE_FAILED: Final = "image_artifact_write_failed"
IMAGE_PROVIDER_NETWORK: Final = "image_provider_network"
IMAGE_PROVIDER_NO_IMAGE: Final = "image_provider_no_image"
IMAGE_REFERENCE_INVALID: Final = "image_reference_invalid"
CODEX_IMAGE_TOOL: Final = "image_generation"
DEFAULT_IMAGE_REASONING: Final = "xhigh"


@dataclass(frozen=True, slots=True)
class ImageReferenceInput:
    path: Path
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    sha256: str


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    prompt: str
    destination: Path
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = DEFAULT_IMAGE_REASONING
    reference_images: tuple[ImageReferenceInput, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    path: Path
    mime_type: str
    model: str
    sha256: str


class ImageGenerationPort(Protocol):
    def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...


class CodexOAuthPort(Protocol):
    def refresh_if_needed(self) -> OAuthCredential: ...


@dataclass(frozen=True, slots=True)
class CodexImageGenerator:
    http: HttpClient
    oauth: CodexOAuthPort

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        credential = self.oauth.refresh_if_needed()
        response = self._request(request, credential)
        if response.status_code != HTTP_OK:
            detail = _response_detail(response)
            message = f"GPT image provider returned HTTP {response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise ProviderError(IMAGE_PROVIDER_HTTP, message)
        try:
            payload = _response_payload(response)
        except ValidationError as error:
            raise ProviderError(
                IMAGE_PROVIDER_RESPONSE,
                "GPT image provider returned invalid JSON",
            ) from error
        image_data, mime_type = _extract_inline_image(payload)
        try:
            content = base64.b64decode(image_data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ProviderError(
                IMAGE_PROVIDER_IMAGE_INVALID,
                "GPT image provider returned invalid base64 image data",
            ) from error
        _verify_image(content)
        try:
            request.destination.parent.mkdir(parents=True, exist_ok=True)
            _ = request.destination.write_bytes(content)
        except OSError as error:
            raise ProviderError(
                IMAGE_ARTIFACT_WRITE_FAILED,
                f"GPT image artifact could not be written: {request.destination}",
            ) from error
        return GeneratedImage(
            path=request.destination,
            mime_type=mime_type,
            model=request.model,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def _request(
        self,
        request: ImageGenerationRequest,
        credential: OAuthCredential,
    ) -> HttpResponse:
        input_content: JsonValue = request.prompt
        image_tool: dict[str, JsonValue] = {"type": CODEX_IMAGE_TOOL}
        if request.reference_images:
            content: list[JsonValue] = [{"type": "input_text", "text": request.prompt}]
            content.extend(
                {
                    "type": "input_image",
                    "image_url": _reference_data_url(reference),
                }
                for reference in request.reference_images
            )
            input_content = content
            image_tool.update(action="edit")
        payload: dict[str, JsonValue] = {
            "model": request.model,
            "input": [{"role": "user", "content": input_content}],
            "tools": [image_tool],
            "reasoning": {"effort": request.reasoning_effort},
            "store": False,
            "stream": True,
        }
        headers = _auth_headers(credential)
        try:
            return self.http.post_json(
                CODEX_IMAGE_URL,
                _JSON_OBJECT.validate_python(payload),
                headers,
            )
        except OAuthError:
            raise
        except httpx2.HTTPError as error:
            raise ProviderError(
                IMAGE_PROVIDER_NETWORK,
                "GPT image provider request failed",
            ) from error


def _reference_data_url(reference: ImageReferenceInput) -> str:
    try:
        content = reference.path.read_bytes()
    except OSError as error:
        raise ProviderError(
            IMAGE_REFERENCE_INVALID,
            f"image reference could not be read: {reference.path}",
        ) from error
    if hashlib.sha256(content).hexdigest() != reference.sha256:
        raise ProviderError(
            IMAGE_REFERENCE_INVALID,
            f"image reference digest does not match: {reference.path}",
        )
    _verify_image(content)
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{reference.mime_type};base64,{encoded}"


def _auth_headers(credential: OAuthCredential) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"{credential.token_type} {credential.access_token}",
        "Content-Type": "application/json",
        "User-Agent": "trace-agent/0.1.0",
    }
    if credential.account_id:
        headers["chatgpt-account-id"] = credential.account_id
    return headers


def _extract_inline_image(payload: JsonObject) -> tuple[str, str]:
    outputs = payload.get("output")
    if isinstance(outputs, list):
        for output in outputs:
            if not isinstance(output, dict) or output.get("type") != "image_generation_call":
                continue
            result = output.get("result")
            if isinstance(result, str):
                return result, "image/png"
    raise ProviderError(
        IMAGE_PROVIDER_NO_IMAGE,
        "GPT image provider returned no image_generation_call result",
    )


def _verify_image(content: bytes) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            _ = image.verify()
    except (OSError, UnidentifiedImageError, SyntaxError) as error:
        raise ProviderError(
            IMAGE_PROVIDER_IMAGE_INVALID,
            "GPT image provider returned an unreadable image",
        ) from error


def _response_detail(response: HttpResponse) -> str:
    try:
        payload = response.json_object()
    except ValidationError:
        return ""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message[:500]
    if isinstance(error, str):
        return error[:500]
    return ""


def _response_payload(response: HttpResponse) -> JsonObject:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type and b"event:" not in response.content:
        return response.json_object()
    output_items: list[JsonValue] = []
    response_payload: JsonObject = {}
    for block in response.content.decode("utf-8", errors="replace").split("\n\n"):
        event_name = next(
            (line[6:].strip() for line in block.splitlines() if line.startswith("event:")),
            "",
        )
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        data = "\n".join(data_lines)
        if not data or data == "[DONE]":
            continue
        try:
            event = _JSON_OBJECT.validate_json(data)
        except ValidationError:
            continue
        event_type = event.get("type")
        if event_name == "response.output_item.done" or event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                output_items.append(item)
        elif event_name == "response.completed" or event_type == "response.completed":
            completed = event.get("response")
            if isinstance(completed, dict):
                response_payload = _JSON_OBJECT.validate_python(completed)
    if output_items:
        response_payload = dict(response_payload)
        response_payload["output"] = output_items
    return response_payload
