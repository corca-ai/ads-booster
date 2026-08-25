from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    ExportBinding,
    path_has_symlink_component,
)
from trace_capture.contracts import CaptureProvenance, ComponentExportManifest, ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


def validate_component_png(
    artifact: Path,
    binding: ExportBinding,
    source_modified_at_ns: int,
    manifest: ComponentExportManifest | None = None,
) -> CaptureProvenance:
    try:
        if path_has_symlink_component(artifact):
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="Trace component export path contains a symlink",
            )
        with Image.open(artifact) as image:
            _ = image.load()
            image_format = image.format
            bands = image.getbands()
            width, height = image.size
            if image_format != "PNG" or "A" not in bands:
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Trace component export must be an alpha PNG",
                )
            if manifest is not None and (width, height) != (manifest.width, manifest.height):
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Trace component export canvas does not match its manifest",
                )
            if binding.expected_width is not None and (
                width,
                height,
            ) != (binding.expected_width, binding.expected_height):
                raise CaptureAdapterError(
                    code=ErrorCode.EXPORT_INVALID,
                    message="Trace component export canvas does not match the capture contract",
                )
            alpha_histogram: list[int] = image.getchannel("A").histogram()
        content = artifact.read_bytes()
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export is not a valid PNG",
        ) from error

    artifact_digest = sha256(content).hexdigest()
    pixel_count = width * height
    transparent_pixels = alpha_histogram[0]
    visible_pixels = sum(alpha_histogram[1:])
    if transparent_pixels * 5 < pixel_count or visible_pixels * 100 < pixel_count:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export must contain transparent and visible regions",
        )

    if manifest is None:
        return CaptureProvenance(
            request_sha256=binding.request_sha256,
            artifact_sha256=artifact_digest,
            bundle_id=binding.bundle_id,
            device_udid=binding.device_udid,
            session_id=binding.session_id,
            byte_size=len(content),
            width=width,
            height=height,
            source_modified_at_ns=source_modified_at_ns,
            native_export_nonce=None,
            native_export_binding_verified=False,
        )

    expected_nonce = binding.export_nonce
    if (
        manifest.request_sha256 != binding.request_sha256
        or manifest.export_nonce != expected_nonce
        or manifest.bundle_id != binding.bundle_id
        or manifest.device_udid != binding.device_udid
        or manifest.role != "trace_components"
    ):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export manifest does not match the capture binding",
        )
    if artifact_digest != manifest.artifact_sha256:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export bytes do not match its native manifest",
        )

    return CaptureProvenance(
        request_sha256=manifest.request_sha256,
        artifact_sha256=artifact_digest,
        bundle_id=manifest.bundle_id,
        device_udid=manifest.device_udid,
        session_id=binding.session_id,
        byte_size=len(content),
        width=width,
        height=height,
        source_modified_at_ns=source_modified_at_ns,
        native_export_nonce=manifest.export_nonce,
        native_export_binding_verified=True,
    )
