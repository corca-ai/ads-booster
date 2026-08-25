from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from pydantic import ValidationError

from trace_capture.capture.capture_safety import CaptureAdapterError, path_has_symlink_component
from trace_capture.contracts import ComponentExportFailure, ComponentExportManifest, ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


def read_component_export_manifest(path: Path) -> ComponentExportManifest:
    _reject_symlink_path(path)
    try:
        raw_manifest = path.read_text(encoding="utf-8")
        return ComponentExportManifest.model_validate_json(raw_manifest)
    except (OSError, UnicodeError, ValidationError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export manifest is invalid",
        ) from error


def raise_export_failure(path: Path) -> NoReturn:
    _reject_symlink_path(path)
    try:
        failure = ComponentExportFailure.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export failure marker is invalid",
        ) from error
    raise CaptureAdapterError(code=ErrorCode.EXPORT_FAILED, message=failure.message)


def _reject_symlink_path(path: Path) -> None:
    if path_has_symlink_component(path):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export path contains a symlink",
        )
