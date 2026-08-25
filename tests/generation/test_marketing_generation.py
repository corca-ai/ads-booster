from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image

from trace_capture.auth.models import OAuthCredential
from trace_capture.contracts import CaptureProvenance
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.planning.scene_planner import ScenePlanner
from trace_capture.providers.image_generation import (
    CodexImageGenerator,
    GeneratedImage,
    ImageGenerationRequest,
    ImageReferenceInput,
)
from trace_capture.runtime.generate_one import (
    GenerateOneOptions,
    GenerateOneRunner,
)
from trace_capture.search.image.background import SearchedBackground
from trace_capture.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trace_capture.capture.capture_safety import CaptureControl
    from trace_capture.capture.worker import CaptureRequest
    from trace_capture.contracts.models import DeviceTarget
    from trace_capture.transport.json_types import JsonObject


def _empty_headers() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class RecordingOAuth:
    credential: OAuthCredential

    def refresh_if_needed(self) -> OAuthCredential:
        return self.credential


@dataclass(slots=True)
class RecordingHttp:
    response: HttpResponse
    url: str = ""
    payload: JsonObject | None = None
    headers: Mapping[str, str] = field(default_factory=_empty_headers)

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        _ = (url, headers)
        message = "unexpected GET"
        raise AssertionError(message)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        self.url = url
        self.payload = payload
        self.headers = headers
        return self.response

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected form request"
        raise AssertionError(message)


@dataclass(frozen=True, slots=True)
class FixtureImageGenerator:
    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (4, 6), (12, 24, 48))
        image.save(request.destination, format="PNG")
        return GeneratedImage(
            path=request.destination,
            mime_type="image/png",
            model=request.model,
            sha256="fixture",
        )


@dataclass(slots=True)
class RecordingImageGenerator:
    request: ImageGenerationRequest | None = None
    requests: list[ImageGenerationRequest] = field(default_factory=list)

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.request = request
        self.requests.append(request)
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 6), (12, 24, 48)).save(request.destination, format="PNG")
        return GeneratedImage(
            path=request.destination,
            mime_type="image/png",
            model=request.model,
            sha256="fixture",
        )


@dataclass(frozen=True, slots=True)
class FixtureBackgroundFetcher:
    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 6), (12, 24, 48)).save(destination, format="PNG")
        return SearchedBackground(
            path=destination,
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            query=query,
            provider="fixture-search",
            image_url="https://images.example/background.png",
            source_url="https://example.com/background",
        )


@dataclass(slots=True)
class RecordingBackgroundFetcher:
    queries: list[str] = field(default_factory=list)

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        self.queries.append(query)
        return FixtureBackgroundFetcher().fetch(query, destination)


@dataclass(slots=True)
class RecordingReadiness:
    devices: list[str] = field(default_factory=list)

    def ensure(self, device: DeviceTarget, control: CaptureControl) -> None:
        del control
        self.devices.append(device.udid)


@dataclass(frozen=True, slots=True)
class FixtureCaptureAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (4, 6), (0, 0, 0, 0))
        image.putpixel((2, 3), (255, 255, 255, 255))
        image.save(request.destination, format="PNG")
        content = request.destination.read_bytes()
        return CaptureProvenance(
            request_sha256="a" * 64,
            artifact_sha256=hashlib.sha256(content).hexdigest(),
            bundle_id="com.corca.Trace",
            device_udid=request.device.udid,
            session_id="fixture-session",
            byte_size=len(content),
            width=4,
            height=6,
            source_modified_at_ns=1,
            source="offline_fixture",
        )


def context() -> MarketingContextBundle:
    return MarketingContextBundle.model_validate(
        {
            "schema_version": "trace.marketing-context.v1",
            "request_id": "jp-student-exam",
            "persona": {
                "persona_id": "jp-university-student",
                "country": "JP",
                "locale": "ja-JP",
                "age_group": "20s",
                "occupation": "university_student",
                "traits": ["diligent", "cute"],
                "interests": ["study", "cafe"],
            },
            "promotion_material": {
                "promotion_material_id": "lock-screen-schedule",
                "feature": "lock_screen_schedule",
                "concept": "exam_week",
                "tone": ["warm", "focused"],
            },
            "reference_date": "2026-08-25T00:00:00Z",
            "device": {
                "kind": "simulator",
                "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
                "platform_version": "26.5",
                "device_name": "iPhone 17 Pro",
            },
        }
    )


def system_ui_asset(tmp_path: Path) -> Path:
    path = tmp_path / "system-ui.png"
    image = Image.new("RGB", (4, 6), (0, 0, 0))
    image.putpixel((0, 0), (255, 255, 255))
    image.save(path, format="PNG")
    return path


def test_scene_planner_when_japanese_student_context_creates_three_trace_items() -> None:
    recipe = ScenePlanner().plan(context())

    assert recipe.locale == "ja-JP"
    assert recipe.trace_items == ("統計学 2限", "レポート提出", "ゼミ準備")
    assert recipe.context.persona_id == "jp-university-student"


def test_scene_planner_when_promotion_supplies_trace_items_then_it_uses_them() -> None:
    # Given a context whose promotion material owns the exact Trace surface copy
    payload = context().model_dump(mode="json")
    payload["promotion_material"]["trace_items"] = [
        "시험까지 7일",
        "오늘 복습 마치기",
        "내일 모의고사",
    ]
    bundle = MarketingContextBundle.model_validate(payload)

    # When the scene is planned
    recipe = ScenePlanner().plan(bundle)

    # Then the supplied promotion copy reaches the native Trace component contract
    assert recipe.trace_items == ("시험까지 7일", "오늘 복습 마치기", "내일 모의고사")


def test_codex_image_generator_when_response_contains_image_call_then_it_writes_artifact(
    tmp_path: Path,
) -> None:
    image_buffer = io.BytesIO()
    Image.new("RGB", (4, 6), (12, 24, 48)).save(image_buffer, format="PNG")
    encoded = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    http = RecordingHttp(
        HttpResponse(
            200,
            json.dumps(
                {
                    "output": [
                        {
                            "type": "image_generation_call",
                            "result": encoded,
                        }
                    ]
                }
            ).encode(),
            {},
        )
    )
    oauth = RecordingOAuth(
        OAuthCredential(
            provider="openai-codex",
            access_token="oauth-token",
            refresh_token="refresh-token",
            expires_at=9_999_999_999,
            account_id="account-01",
        )
    )
    destination = tmp_path / "background.png"

    generated = CodexImageGenerator(http=http, oauth=oauth).generate(
        ImageGenerationRequest(
            prompt="mock background",
            destination=destination,
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
        )
    )

    assert generated.path == destination
    assert generated.mime_type == "image/png"
    assert destination.read_bytes() == image_buffer.getvalue()
    assert http.url.endswith("/backend-api/codex/responses")
    assert http.payload is not None
    assert http.payload["model"] == "gpt-5.6-luna"
    assert http.payload["input"] == [{"role": "user", "content": "mock background"}]
    assert http.payload["tools"] == [{"type": "image_generation"}]
    assert http.payload["reasoning"] == {"effort": "xhigh"}
    assert http.payload["store"] is False
    assert http.payload["stream"] is True
    assert http.headers["Authorization"] == "Bearer oauth-token"
    assert http.headers["chatgpt-account-id"] == "account-01"


def test_codex_image_generator_when_reference_exists_then_it_requests_an_image_edit(
    tmp_path: Path,
) -> None:
    # Given a verified local reference image and a successful provider image response
    reference = tmp_path / "reference.png"
    Image.new("RGB", (4, 6), (80, 120, 160)).save(reference, format="PNG")
    reference_digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    output_buffer = io.BytesIO()
    Image.new("RGB", (4, 6), (12, 24, 48)).save(output_buffer, format="PNG")
    http = RecordingHttp(
        HttpResponse(
            200,
            json.dumps(
                {
                    "output": [
                        {
                            "type": "image_generation_call",
                            "result": base64.b64encode(output_buffer.getvalue()).decode("ascii"),
                        }
                    ]
                }
            ).encode(),
            {},
        )
    )
    oauth = RecordingOAuth(
        OAuthCredential(
            provider="openai-codex",
            access_token="oauth-token",
            refresh_token="refresh-token",
            expires_at=9_999_999_999,
            account_id="account-01",
        )
    )

    # When image generation receives the reference
    _ = CodexImageGenerator(http=http, oauth=oauth).generate(
        ImageGenerationRequest(
            prompt="create a distinct campaign variation",
            destination=tmp_path / "output.png",
            model="gpt-5.6-luna",
            reference_images=(
                ImageReferenceInput(
                    path=reference,
                    mime_type="image/png",
                    sha256=reference_digest,
                ),
            ),
        )
    )

    # Then the Responses request carries the image input and selects high-fidelity editing
    assert http.payload is not None
    input_items = http.payload["input"]
    assert isinstance(input_items, list)
    message = input_items[0]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    text_item = content[0]
    image_item = content[1]
    assert isinstance(text_item, dict)
    assert isinstance(image_item, dict)
    assert text_item.get("type") == "input_text"
    assert image_item.get("type") == "input_image"
    image_url = image_item.get("image_url")
    assert isinstance(image_url, str)
    assert image_url.startswith("data:image/png;base64,")
    assert http.payload["tools"] == [
        {"type": "image_generation", "action": "edit"}
    ]


def test_generate_one_runner_when_context_is_valid_then_it_completes_one_image(
    tmp_path: Path,
) -> None:
    system_ui = system_ui_asset(tmp_path)
    options = GenerateOneOptions(
        output_root=tmp_path / "generated",
        state_root=tmp_path / "state",
        capture_output_root=tmp_path / "capture",
        iphone_ui_path=system_ui,
        appium_server="http://127.0.0.1:4723",
        timeout_seconds=30,
    )

    result = GenerateOneRunner(
        options=options,
        background_fetcher=FixtureBackgroundFetcher(),
        capture_adapter=FixtureCaptureAdapter(),
    ).run(context())

    assert result.state.value == "completed"
    assert result.output_image == "outputs/final.png"
    assert (tmp_path / "generated" / "jp-student-exam" / "outputs" / "final.png").is_file()
    assert (tmp_path / "generated" / "jp-student-exam" / "inputs" / "iphone-ui.png").is_file()


def test_generate_one_runner_when_capture_is_ready_then_it_searches_one_background(
    tmp_path: Path,
) -> None:
    # Given a valid context and a capture adapter that produces the native Trace layer
    background_fetcher = RecordingBackgroundFetcher()
    readiness = RecordingReadiness()
    system_ui = system_ui_asset(tmp_path)

    # When the complete generation runner executes the bundle
    _ = GenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            state_root=tmp_path / "state",
            capture_output_root=tmp_path / "capture",
            iphone_ui_path=system_ui,
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
            capture_readiness=readiness,
        ),
        background_fetcher=background_fetcher,
        capture_adapter=FixtureCaptureAdapter(),
    ).run(context())

    # Then the background is searched once and composition receives the system UI layer
    assert len(background_fetcher.queries) == 1
    assert "vertical lifestyle photo" in background_fetcher.queries[0]
    assert (tmp_path / "generated" / "jp-student-exam" / "inputs" / "iphone-ui.png").is_file()
    assert readiness.devices == ["E1FB798D-79E6-4B25-A987-D298A4FD122A"]


def test_generate_one_runner_when_bundle_has_reference_then_it_uses_search_not_image_edit(
    tmp_path: Path,
) -> None:
    # Given a bundle bound to a verified workspace reference image
    reference = tmp_path / "assets" / "reference.png"
    reference.parent.mkdir()
    Image.new("RGB", (4, 6), (80, 120, 160)).save(reference, format="PNG")
    payload = context().model_dump(mode="json")
    payload["reference_images"] = [
        {
            "reference_id": "reference-one",
            "relative_path": "assets/reference.png",
            "media_type": "image/png",
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        }
    ]
    bundle = MarketingContextBundle.model_validate(payload)
    background_fetcher = RecordingBackgroundFetcher()
    system_ui = system_ui_asset(tmp_path)

    # When the complete generation runner executes the bundle
    _ = GenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            state_root=tmp_path / "state",
            capture_output_root=tmp_path / "capture",
            iphone_ui_path=system_ui,
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
        ),
        background_fetcher=background_fetcher,
        capture_adapter=FixtureCaptureAdapter(),
    ).run(bundle)

    # Then the search query is constructed from context rather than the image-edit reference
    assert len(background_fetcher.queries) == 1
