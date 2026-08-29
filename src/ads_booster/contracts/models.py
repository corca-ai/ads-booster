from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"),
]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Locale = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$")]
TraceItem = Annotated[str, Field(min_length=1, max_length=80)]


def require_safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        error_type = "unsafe_relative_path"
        error_message = "file paths must stay inside their declared root"
        raise PydanticCustomError(
            error_type,
            error_message,
        )
    return value


RelativePath = Annotated[
    str,
    Field(min_length=1, max_length=240),
    AfterValidator(require_safe_relative_path),
]


class ContractModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class DeviceKind(StrEnum):
    SIMULATOR = "simulator"
    PHYSICAL = "physical"


class DeviceTarget(ContractModel):
    kind: DeviceKind
    udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    platform_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    device_name: Annotated[str, Field(min_length=1, max_length=80)]


class ErrorCode(StrEnum):
    INPUT_ASSET_MISSING = "input_asset_missing"
    APPIUM_UNAVAILABLE = "appium_unavailable"
    SCENE_CAPTURE_FAILED = "scene_capture_failed"
    APPIUM_ENDPOINT_REJECTED = "appium_endpoint_rejected"
    CAPTURE_TIMED_OUT = "capture_timed_out"
    CAPTURE_CANCELLED = "capture_cancelled"
    CAPTURE_LEASE_UNAVAILABLE = "capture_lease_unavailable"
    EXPORT_STALE = "export_stale"
    EXPORT_UNVERIFIED = "export_unverified"
    EXPORT_INVALID = "export_invalid"
    EXPORT_FAILED = "export_failed"


Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class CaptureProvenance(ContractModel):
    request_sha256: Sha256Digest
    artifact_sha256: Sha256Digest
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]+$")]
    device_udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    session_id: Annotated[str, Field(min_length=1, max_length=160)]
    byte_size: Annotated[int, Field(gt=0)]
    width: Annotated[int, Field(gt=0, le=8192)]
    height: Annotated[int, Field(gt=0, le=8192)]
    source_modified_at_ns: Annotated[int, Field(gt=0)]
    source: Literal["native_appium"] = "native_appium"
    artifact_role: Literal["trace_wallpaper"] = "trace_wallpaper"
    native_export_nonce: Sha256Digest | None = None
    native_export_binding_verified: bool = False
