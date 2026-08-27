from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import Field, ValidationError

from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    path_has_symlink_component,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode, WallpaperExportManifest
from ads_booster.contracts.models import ContractModel

if TYPE_CHECKING:
    from pathlib import Path


class WallpaperExportFailure(ContractModel):
    schema_version: Literal["trace.wallpaper-export-failure.v1"]
    code: Literal["export_failed"]
    message: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class WallpaperExportBinding:
    request_sha256: str
    bundle_id: str
    device_udid: str
    session_id: str
    cleared_at_ns: int
    export_nonce: str = field(default_factory=lambda: secrets.token_hex(32))
    expected_width: int | None = None
    expected_height: int | None = None


def validate_wallpaper_png(
    artifact: Path,
    binding: WallpaperExportBinding,
    source_modified_at_ns: int,
    manifest: WallpaperExportManifest,
) -> CaptureProvenance:
    try:
        reject_symlink_path(artifact)
        with Image.open(artifact) as image:
            _ = image.load()
            width, height = image.size
            if image.format != "PNG" or image.mode not in {"RGB", "RGBA"}:
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Trace wallpaper export must be an RGB or RGBA PNG",
                )
            if image.mode == "RGBA":
                alpha_histogram: list[int] = image.getchannel("A").histogram()
                if alpha_histogram[255] != width * height:
                    raise CaptureAdapterError(
                        code=ErrorCode.EXPORT_INVALID,
                        message="Trace wallpaper export must not contain transparent pixels",
                    )
        content = artifact.read_bytes()
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper export is not a valid PNG",
        ) from error
    _validate_manifest_binding(manifest, binding, width, height, sha256(content).hexdigest())
    return CaptureProvenance(
        request_sha256=manifest.request_sha256,
        artifact_sha256=manifest.artifact_sha256,
        bundle_id=manifest.bundle_id,
        device_udid=manifest.device_udid,
        session_id=binding.session_id,
        byte_size=len(content),
        width=width,
        height=height,
        source_modified_at_ns=source_modified_at_ns,
        artifact_role="trace_wallpaper",
        native_export_nonce=manifest.export_nonce,
        native_export_binding_verified=True,
    )


def read_wallpaper_export_manifest(path: Path) -> WallpaperExportManifest:
    try:
        return WallpaperExportManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper export manifest is invalid",
        ) from error


def raise_if_wallpaper_export_failure(path: Path) -> None:
    if not path.is_file():
        return
    try:
        failure = WallpaperExportFailure.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper export failure marker is invalid",
        ) from error
    raise CaptureAdapterError(code=ErrorCode.EXPORT_FAILED, message=failure.message)


def file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError as error:
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Trace wallpaper export metadata could not be read",
        ) from error
    else:
        return stat.st_size, stat.st_mtime_ns


def reject_symlink_path(path: Path) -> None:
    if path_has_symlink_component(path):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper export path contains a symlink",
        )


def _validate_manifest_binding(
    manifest: WallpaperExportManifest,
    binding: WallpaperExportBinding,
    width: int,
    height: int,
    artifact_sha256: str,
) -> None:
    if (
        manifest.request_sha256 != binding.request_sha256
        or manifest.export_nonce != binding.export_nonce
        or manifest.bundle_id != binding.bundle_id
        or manifest.device_udid != binding.device_udid
        or manifest.role != "trace_wallpaper"
        or manifest.artifact_sha256 != artifact_sha256
        or (manifest.width, manifest.height) != (width, height)
        or (binding.expected_width is not None and binding.expected_width != width)
        or (binding.expected_height is not None and binding.expected_height != height)
    ):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper export does not match its capture binding",
        )
