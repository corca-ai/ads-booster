from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.imagegen_artifact import (
    parse_generated_image_path,
    validate_source_and_destination,
    write_normalized_png,
)
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.transport.json_types import JsonObject

_DEFAULT_TIMEOUT_SECONDS: Final = 900.0
_PROMPT_VERSION: Final = "trace.ios-lock-screen-image-edit.v1"
_IMAGE_PATH_MAX_LENGTH: Final = 4_096
_CODEX_FAILED: Final = "Codex ImageGen edit failed"
_DIGEST_FAILED: Final = "Codex ImageGen artifact digest could not be read"


class StructuredCodexImageEdit(Protocol):
    def run_image_edit_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        image: Path,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class ImageEditProvenance:
    source_path: Path
    destination_path: Path
    generated_image_path: Path
    source_sha256: str
    artifact_sha256: str
    request_sha256: str
    export_nonce: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CodexImageGenEditor:
    codex: StructuredCodexImageEdit
    generated_images_root: Path | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def edit(
        self,
        source: Path,
        destination: Path,
        contract: CodexAppiumJobContract,
        control: CaptureControl,
    ) -> ImageEditProvenance:
        """Use one Codex ImageGen edit and copy its PNG to the native dimensions."""
        control.checkpoint()
        source_path, destination_path, source_size = validate_source_and_destination(
            source,
            destination,
        )
        workspace = destination_path.parent
        timeout = min(control.remaining_seconds(), self.timeout_seconds)
        try:
            raw_result = self.codex.run_image_edit_job(
                build_image_edit_prompt(contract),
                _image_edit_schema(),
                image=source_path,
                workspace=workspace,
                timeout_seconds=timeout,
            )
        except CaptureAdapterError:
            raise
        except (OSError, RuntimeError) as error:
            raise _image_edit_error(_CODEX_FAILED) from error
        control.checkpoint()
        generated_path = parse_generated_image_path(raw_result, self._generated_images_root())
        write_normalized_png(generated_path, source_path, destination_path, source_size)
        try:
            source_digest = sha256(source_path.read_bytes()).hexdigest()
            artifact_digest = sha256(destination_path.read_bytes()).hexdigest()
        except OSError as error:
            raise _image_edit_error(_DIGEST_FAILED) from error
        control.checkpoint()
        return ImageEditProvenance(
            source_path=source_path,
            destination_path=destination_path,
            generated_image_path=generated_path,
            source_sha256=source_digest,
            artifact_sha256=artifact_digest,
            request_sha256=contract.request_sha256,
            export_nonce=contract.export_nonce,
            width=source_size[0],
            height=source_size[1],
        )

    def _generated_images_root(self) -> Path:
        if self.generated_images_root is not None:
            return self.generated_images_root.expanduser()
        codex_home = os.environ.get("CODEX_HOME")
        home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
        return home / "generated_images"


def build_image_edit_prompt(contract: CodexAppiumJobContract) -> str:
    """Build the constrained edit instruction for the real iOS lock-screen result."""
    return (
        f'<trace-image-edit version="{_PROMPT_VERSION}">\n'
        "Edit the supplied Trace-native wallpaper into the real iOS lock-screen view shown "
        "after the wallpaper has been set and the device is locked.\n"
        "<target>real-ios-lock-screen-after-wallpaper-setup</target>\n"
        "<preserve>trace-background|trace-calendar-cards|trace-text</preserve>\n"
        "<add>date|clock|status-icons|bottom-shortcuts</add>\n"
        "<exclude>dynamic-island|notch|device-frame|settings-buttons</exclude>\n"
        "Keep every existing Trace card and character exactly readable; do not replace, "
        "rewrite, crop away, or invent any Trace content. Do not show the wallpaper settings "
        "editor or its Cancel, Add, or Customize controls. Do not draw a black pill, notch, "
        "cutout, bezel, or device frame anywhere; use the supplied canvas as the full screen. "
        "Match the supplied background's "
        "composition and contrast while adding only the lock-screen UI in the top and bottom "
        "screen areas; leave the middle Trace cards pixel-for-pixel unchanged.\n"
        f"<locale>{contract.locale}</locale>\n"
        f"<time-zone>{contract.time_zone}</time-zone>\n"
        f"<reference-date>{contract.context.reference_date.isoformat()}</reference-date>\n"
        f"<device>{contract.device.device_name}</device>\n"
        "Return only the schema-conforming JSON response with the generated PNG path.\n"
        "</trace-image-edit>"
    )


def _image_edit_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "minLength": 1,
                "maxLength": _IMAGE_PATH_MAX_LENGTH,
            }
        },
        "required": ["image_path"],
        "additionalProperties": False,
    }


def _image_edit_error(message: str) -> CaptureAdapterError:
    return CaptureAdapterError(code=ErrorCode.SCENE_CAPTURE_FAILED, message=message)


__all__ = [
    "CodexImageGenEditor",
    "ImageEditProvenance",
    "StructuredCodexImageEdit",
    "build_image_edit_prompt",
]
