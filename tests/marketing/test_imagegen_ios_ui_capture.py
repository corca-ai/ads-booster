from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.capture.codex_imagegen_ui import (
    ImagegenIosUiArtifact,
    ImagegenIosUiCaptureRequest,
)
from ads_booster.contracts.native_export import ImagegenIosUiManifest
from ads_booster.marketing.native_capture import HostedWorkspaceCaptureExecutor
from tests.marketing.test_native_capture import (
    FakeDeviceResolver,
    RecordingAppiumAdapter,
    RecordingBackgroundPreparer,
    task_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class RecordingImagegenIosUiCapture:
    calls: list[str]

    def capture(self, request: ImagegenIosUiCaptureRequest) -> ImagegenIosUiArtifact:
        request.control.checkpoint()
        self.calls.append("ios_ui")
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 20), "red").save(request.destination, format="PNG")
        layer_path = request.destination.with_name(
            f"{request.destination.stem}.imagegen-ui-layer.png"
        )
        Image.new("RGBA", (12, 20), (255, 255, 255, 128)).save(layer_path, format="PNG")
        return ImagegenIosUiArtifact(
            manifest=ImagegenIosUiManifest(
                schema_version="trace.imagen-ios-ui.v1",
                request_sha256=request.request_sha256,
                export_nonce=request.export_nonce,
                device_udid=request.context.device.udid,
                source_trace_artifact_sha256=sha256(
                    request.source_trace_wallpaper.read_bytes()
                ).hexdigest(),
                imagegen_prompt_sha256="f" * 64,
                imagegen_ui_layer_sha256=sha256(layer_path.read_bytes()).hexdigest(),
                artifact_sha256=sha256(request.destination.read_bytes()).hexdigest(),
                width=12,
                height=20,
            ),
            ui_layer_path=layer_path,
        )


def test_capture_when_imagegen_ui_layer_is_enabled_then_callback_uses_imagegen_png(
    tmp_path: Path,
) -> None:
    # Given a valid Trace export and a deterministic iOS UI capture stage
    calls: list[str] = []
    executor = HostedWorkspaceCaptureExecutor(
        background_preparer=RecordingBackgroundPreparer(calls),
        appium=RecordingAppiumAdapter(calls),
        ios_ui=RecordingImagegenIosUiCapture(calls),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    )
    prepared = executor.prepare(task_fixture())

    # When the hosted worker captures one candidate
    result = executor.execute(prepared)

    # Then it preserves Trace PNG locally but returns the ImageGen final PNG
    final_path = prepared.output.with_name("imagen_ios_ui.png")
    assert calls == ["background", "ready", "execute", "ios_ui"]
    assert prepared.output.is_file()
    assert final_path.is_file()
    assert result.output["artifact_role"] == "imagen_ios_ui"
    assert result.output["capture_source"] == "imagen_ios_ui"
    assert (
        result.output["source_trace_artifact_sha256"]
        == sha256(prepared.output.read_bytes()).hexdigest()
    )
    assert result.output["image_sha256"] == sha256(final_path.read_bytes()).hexdigest()
