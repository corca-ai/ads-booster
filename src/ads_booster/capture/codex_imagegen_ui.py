from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, Protocol

from PIL import Image, UnidentifiedImageError

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.ios_lock_screen_layer import compose_ios_lock_screen_layer
from ads_booster.contracts.models import ErrorCode
from ads_booster.contracts.native_export import ImagegenIosUiManifest

if TYPE_CHECKING:
    from ads_booster.contracts.generation import MarketingContextBundle

_GENERATED_LAYER_NAME: Final = "ios_ui_layer.png"
_IMAGE_GENERATION_FEATURE: Final = "image_generation"
_PRIVATE_FILE_MODE: Final = 0o600


class _RawImage(Protocol):
    def tobytes(self, encoder_name: str = "raw") -> bytes: ...


class ImagegenCommandRunner(Protocol):
    def run(self, command: tuple[str, ...], prompt: str, timeout_seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class SubprocessImagegenCommandRunner:
    def run(self, command: tuple[str, ...], prompt: str, timeout_seconds: float) -> None:
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex ImageGen command is unavailable",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message="Codex ImageGen command exceeded the capture deadline",
            ) from error
        if completed.returncode != 0:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex ImageGen command failed",
            )


@dataclass(frozen=True, slots=True)
class ImagegenIosUiCaptureRequest:
    context: MarketingContextBundle
    source_trace_wallpaper: Path
    destination: Path
    request_sha256: str
    export_nonce: str
    control: CaptureControl


@dataclass(frozen=True, slots=True)
class ImagegenIosUiArtifact:
    manifest: ImagegenIosUiManifest
    ui_layer_path: Path


@dataclass(frozen=True, slots=True)
class CodexImagegenIosUiLayer:
    executable: Path
    reference_image: Path
    runner: ImagegenCommandRunner = field(default_factory=SubprocessImagegenCommandRunner)

    def capture(self, request: ImagegenIosUiCaptureRequest) -> ImagegenIosUiArtifact:
        prompt = _imagegen_prompt(request.context)
        if not self.reference_image.is_file():
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="Codex ImageGen iPhone UI reference is unavailable",
            )
        with TemporaryDirectory(prefix="trace-codex-imagegen-") as directory:
            workspace = Path(directory)
            self.runner.run(
                _imagegen_command(self.executable, self.reference_image, workspace),
                prompt,
                request.control.remaining_seconds(),
            )
            request.control.checkpoint()
            layer = _normalized_layer(
                _read_transparent_png(workspace / _GENERATED_LAYER_NAME),
                request,
            )
        wallpaper = _read_opaque_png(request.source_trace_wallpaper)
        final = compose_ios_lock_screen_layer(wallpaper, layer)
        return _write_artifact(request, prompt, layer, final)


def _imagegen_command(
    executable: Path,
    reference_image: Path,
    workspace: Path,
) -> tuple[str, ...]:
    return (
        str(executable),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--enable",
        _IMAGE_GENERATION_FEATURE,
        "--image",
        str(reference_image),
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "-",
    )


def _imagegen_prompt(context: MarketingContextBundle) -> str:
    reference = context.reference_date
    date_text = f"{_korean_weekday(reference.weekday())}, {reference.month}월 {reference.day}일"
    time_text = reference.strftime("%H:%M")
    return (
        "Use the image generation tool to create exactly one transparent PNG named "
        f"{_GENERATED_LAYER_NAME} in the current directory. "
        "Use the attached reference image as the visual specification. "
        "Generate only a default iPhone lock-screen date/time UI layer with "
        "the same neutral white color, flat system-style typography, hierarchy, spacing, and top "
        "placement. Do not personalize, recolor, outline, glow, shadow, decorate, or stylize it. "
        f"Replace only the text with exact date {date_text!r} and exact time {time_text!r}. "
        "No background, phone frame, status bar, widgets, notifications, icons, logos, watermark, "
        "or extra words. Preserve a genuinely transparent background."
    )


def _korean_weekday(weekday: int) -> str:
    return ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")[weekday]


def _read_transparent_png(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            _ = image.load()
            if image.format != "PNG":
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Codex ImageGen must return a PNG UI layer",
                )
            layer = image.convert("RGBA")
    except (OSError, UnidentifiedImageError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Codex ImageGen UI layer is unreadable",
        ) from error
    _red, _green, _blue, alpha = layer.split()
    alpha_bytes = _read_raw_bytes(alpha)
    if 0 not in alpha_bytes or max(alpha_bytes) == 0:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Codex ImageGen UI layer must have transparent and visible pixels",
        )
    return layer


def _read_raw_bytes(image: _RawImage) -> bytes:
    return image.tobytes()


def _normalized_layer(image: Image.Image, request: ImagegenIosUiCaptureRequest) -> Image.Image:
    wallpaper = _read_opaque_png(request.source_trace_wallpaper)
    width, height = wallpaper.size
    resized = image.resize(
        (width, round(image.height * width / image.width)),
        Image.Resampling.LANCZOS,
    )
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.alpha_composite(resized, dest=(0, 0))
    return layer


def _read_opaque_png(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            _ = image.load()
            if image.format != "PNG":
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Trace wallpaper source must be a PNG",
                )
            return image.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper source is unreadable",
        ) from error


def _write_artifact(
    request: ImagegenIosUiCaptureRequest,
    prompt: str,
    layer: Image.Image,
    final: Image.Image,
) -> ImagegenIosUiArtifact:
    request.destination.parent.mkdir(parents=True, exist_ok=True)
    layer_path = request.destination.with_name(f"{request.destination.stem}.imagegen-ui-layer.png")
    final.save(request.destination, format="PNG")
    layer.save(layer_path, format="PNG")
    final_bytes = request.destination.read_bytes()
    manifest = ImagegenIosUiManifest(
        schema_version="trace.imagen-ios-ui.v1",
        request_sha256=request.request_sha256,
        export_nonce=request.export_nonce,
        device_udid=request.context.device.udid,
        source_trace_artifact_sha256=sha256(
            request.source_trace_wallpaper.read_bytes()
        ).hexdigest(),
        imagegen_prompt_sha256=sha256(prompt.encode()).hexdigest(),
        imagegen_ui_layer_sha256=sha256(layer_path.read_bytes()).hexdigest(),
        artifact_sha256=sha256(final_bytes).hexdigest(),
        width=final.width,
        height=final.height,
    )
    manifest_path = request.destination.with_suffix(".manifest.json")
    _ = manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    manifest_path.chmod(_PRIVATE_FILE_MODE)
    return ImagegenIosUiArtifact(manifest=manifest, ui_layer_path=layer_path)
