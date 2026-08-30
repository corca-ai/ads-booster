from __future__ import annotations

import io
import stat
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.imagegen_editor import CodexImageGenEditor
from ads_booster.contracts import ErrorCode

from .codex_appium_support import v2_contract

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingImageEdit:
    result: JsonObject
    calls: list[tuple[str, JsonObject, Path, Path, float]] = field(default_factory=list)
    on_call: Callable[[Path], None] | None = None

    def run_image_edit_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        image: Path,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        self.calls.append((prompt, schema, image, workspace, timeout_seconds))
        if self.on_call is not None:
            self.on_call(workspace)
        return self.result


def _write_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def _editor(
    generated_root: Path,
    result: JsonObject,
) -> tuple[CodexImageGenEditor, RecordingImageEdit]:
    codex = RecordingImageEdit(result)
    return CodexImageGenEditor(codex=codex, generated_images_root=generated_root), codex


def test_edit_when_imagegen_returns_png_then_writes_normalized_trace_lock_screen(
    tmp_path: Path,
) -> None:
    # Given a verified native Trace wallpaper and one ImageGen PNG in the allowed root
    generated_root = tmp_path / "generated-images"
    generated = generated_root / "edited.png"
    source = tmp_path / "native.png"
    destination = tmp_path / "final.png"
    _write_png(source, (20, 30), (14, 24, 34))
    _write_png(generated, (40, 60), (80, 90, 100))
    editor, codex = _editor(generated_root, {"image_path": str(generated)})

    # When the adapter performs one Codex ImageGen edit and stores the final artifact
    provenance = editor.edit(
        source,
        destination,
        v2_contract(),
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the output is a PNG at the native dimensions with bound provenance
    with Image.open(destination) as image:
        assert image.format == "PNG"
        assert image.size == (20, 30)
        with image.crop((10, 7, 11, 8)) as top_pixel:
            actual_top = io.BytesIO()
            top_pixel.save(actual_top, format="PNG")
        with image.crop((10, 15, 11, 16)) as middle_pixel:
            actual = io.BytesIO()
            middle_pixel.save(actual, format="PNG")
        expected_top = io.BytesIO()
        Image.new("RGB", (1, 1), (80, 90, 100)).save(expected_top, format="PNG")
        assert actual_top.getvalue() == expected_top.getvalue()
        expected = io.BytesIO()
        Image.new("RGB", (1, 1), (14, 24, 34)).save(expected, format="PNG")
        assert actual.getvalue() == expected.getvalue()
    assert len(codex.calls) == 1
    assert provenance.source_path == source
    assert provenance.destination_path == destination
    assert provenance.generated_image_path == generated
    assert provenance.width == 20
    assert provenance.height == 30
    assert provenance.source_sha256 == sha256(source.read_bytes()).hexdigest()
    assert provenance.artifact_sha256 == sha256(destination.read_bytes()).hexdigest()
    assert provenance.request_sha256 == v2_contract().request_sha256
    assert provenance.export_nonce == v2_contract().export_nonce
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_edit_when_generated_image_is_outside_codex_root_then_rejects_it(
    tmp_path: Path,
) -> None:
    # Given a valid-looking ImageGen result outside CODEX_HOME/generated_images
    generated_root = tmp_path / "generated-images"
    generated = tmp_path / "outside.png"
    source = tmp_path / "native.png"
    destination = tmp_path / "final.png"
    _write_png(source, (20, 30), (14, 24, 34))
    _write_png(generated, (20, 30), (80, 90, 100))
    editor, codex = _editor(generated_root, {"image_path": str(generated)})

    # When the adapter resolves the untrusted ImageGen path
    with pytest.raises(CaptureAdapterError) as raised:
        _ = editor.edit(
            source,
            destination,
            v2_contract(),
            CaptureControl.start(timeout_seconds=30),
        )

    # Then the path is rejected before any final artifact is written
    assert raised.value.code is ErrorCode.SCENE_CAPTURE_FAILED
    assert not destination.exists()
    assert len(codex.calls) == 1


def test_edit_when_generated_image_is_a_symlink_then_rejects_it(tmp_path: Path) -> None:
    # Given a symlink inside the permitted root that points to an external PNG
    generated_root = tmp_path / "generated-images"
    generated_root.mkdir()
    external = tmp_path / "external.png"
    generated = generated_root / "edited.png"
    source = tmp_path / "native.png"
    destination = tmp_path / "final.png"
    _write_png(source, (20, 30), (14, 24, 34))
    _write_png(external, (20, 30), (80, 90, 100))
    generated.symlink_to(external)
    editor, _codex = _editor(generated_root, {"image_path": str(generated)})

    # When the adapter checks the returned path
    with pytest.raises(CaptureAdapterError, match="symlink"):
        _ = editor.edit(
            source,
            destination,
            v2_contract(),
            CaptureControl.start(timeout_seconds=30),
        )

    # Then no symlink target is copied into the final artifact
    assert not destination.exists()


def test_edit_when_codex_result_has_no_strict_image_path_then_fails_typed(
    tmp_path: Path,
) -> None:
    # Given a Codex response that does not conform to the image-edit response schema
    generated_root = tmp_path / "generated-images"
    source = tmp_path / "native.png"
    destination = tmp_path / "final.png"
    _write_png(source, (20, 30), (14, 24, 34))
    editor, codex = _editor(generated_root, {"status": "completed"})

    # When the adapter parses the structured response
    with pytest.raises(CaptureAdapterError, match="image path"):
        _ = editor.edit(
            source,
            destination,
            v2_contract(),
            CaptureControl.start(timeout_seconds=30),
        )

    # Then malformed output cannot create a final artifact
    assert len(codex.calls) == 1
    assert not destination.exists()


def test_edit_when_prompt_is_built_then_it_requests_real_lock_screen_without_island(
    tmp_path: Path,
) -> None:
    # Given a native wallpaper and an allowed ImageGen output
    generated_root = tmp_path / "generated-images"
    generated = generated_root / "edited.png"
    source = tmp_path / "native.png"
    destination = tmp_path / "final.png"
    _write_png(source, (20, 30), (14, 24, 34))
    _write_png(generated, (20, 30), (80, 90, 100))
    editor, codex = _editor(generated_root, {"image_path": str(generated)})

    # When the adapter builds the model instruction
    _ = editor.edit(
        source,
        destination,
        v2_contract(),
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the machine-readable prompt policy carries the requested UI boundary
    prompt = codex.calls[0][0]
    assert "<target>real-ios-lock-screen-after-wallpaper-setup</target>" in prompt
    assert "<preserve>trace-background|trace-calendar-cards|trace-text</preserve>" in prompt
    assert "<add>date|clock|status-icons|bottom-shortcuts</add>" in prompt
    assert "<exclude>dynamic-island|notch|device-frame|settings-buttons</exclude>" in prompt
    assert "CONTROL_PLANE_TOKEN" not in prompt
