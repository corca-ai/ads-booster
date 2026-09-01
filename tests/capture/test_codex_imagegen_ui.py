from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from PIL import Image

from ads_booster.capture.capture_safety import SYSTEM_CAPTURE_CLOCK, CaptureControl
from ads_booster.capture.codex_imagegen_ui import (
    CodexImagegenIosUiLayer,
    ImagegenIosUiCaptureRequest,
)
from ads_booster.marketing.native_capture import HostedWorkspaceCaptureExecutor
from tests.marketing.test_native_capture import (
    FakeDeviceResolver,
    RecordingAppiumAdapter,
    RecordingBackgroundPreparer,
    task_fixture,
)


@dataclass(frozen=True, slots=True)
class WritingImagegenRunner:
    calls: list[tuple[str, ...]]

    def run(self, command: tuple[str, ...], prompt: str, timeout_seconds: float) -> None:
        del prompt, timeout_seconds
        self.calls.append(command)
        workspace = Path(command[command.index("--cd") + 1])
        layer = Image.new("RGBA", (6, 8), (0, 0, 0, 0))
        layer.putpixel((2, 2), (255, 0, 0, 255))
        layer.save(workspace / "ios_ui_layer.png", format="PNG")


def test_capture_when_codex_imagegen_writes_ui_layer_then_returns_bound_final_png(
    tmp_path: Path,
) -> None:
    # Given a prepared Korean capture context and a deterministic Codex ImageGen runner
    calls: list[str] = []
    prepared = HostedWorkspaceCaptureExecutor(
        background_preparer=RecordingBackgroundPreparer(calls),
        appium=RecordingAppiumAdapter(calls),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    ).prepare(task_fixture())
    source = tmp_path / "trace.png"
    Image.new("RGB", (12, 20), (10, 20, 30)).save(source, format="PNG")
    command_calls: list[tuple[str, ...]] = []
    destination = tmp_path / "imagen_ios_ui.png"
    capture = CodexImagegenIosUiLayer(
        executable=Path("/usr/local/bin/codex"),
        reference_image=source,
        runner=WritingImagegenRunner(command_calls),
    )

    # When the worker asks Codex ImageGen for a transparent date/time layer
    artifact = capture.capture(
        ImagegenIosUiCaptureRequest(
            context=prepared.contract.context,
            source_trace_wallpaper=source,
            destination=destination,
            request_sha256="a" * 64,
            export_nonce="b" * 64,
            control=CaptureControl.start(10, clock=SYSTEM_CAPTURE_CLOCK),
        )
    )

    # Then it records the generated layer and composes a Trace-sized final PNG
    with Image.open(destination) as final:
        final_size = final.size
    assert "image_generation" in command_calls[0]
    assert "--image" in command_calls[0]
    assert final_size == (12, 20)
    assert artifact.ui_layer_path.is_file()
    assert (
        artifact.manifest.imagegen_ui_layer_sha256
        == sha256(artifact.ui_layer_path.read_bytes()).hexdigest()
    )
    assert artifact.manifest.artifact_sha256 == sha256(destination.read_bytes()).hexdigest()
