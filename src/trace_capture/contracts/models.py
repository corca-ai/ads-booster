from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, model_validator
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


class MarketingContext(ContractModel):
    country: CountryCode
    persona_id: Identifier
    promotion_material_id: Identifier
    reference_url: HttpUrl | None = None


class DeviceKind(StrEnum):
    SIMULATOR = "simulator"
    PHYSICAL = "physical"


class DeviceTarget(ContractModel):
    kind: DeviceKind
    udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    platform_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    device_name: Annotated[str, Field(min_length=1, max_length=80)]


class TraceData(ContractModel):
    items: Annotated[tuple[TraceItem, ...], Field(min_length=3, max_length=3)]


class ComponentExportCanvas(ContractModel):
    width: Annotated[int, Field(gt=0, le=8192)]
    height: Annotated[int, Field(gt=0, le=8192)]


class CaptureScene(ContractModel):
    scene_id: Identifier
    locale: Locale
    capture_target: Literal["trace_components"]
    background_image: RelativePath | None = None
    component_canvas: ComponentExportCanvas | None = None
    reference_date: datetime = datetime(2026, 8, 22, tzinfo=UTC)
    trace_data: TraceData


class CaptureJob(ContractModel):
    schema_version: Literal["trace.capture-job.v1"]
    job_id: Identifier
    context: MarketingContext
    device: DeviceTarget
    scenes: Annotated[tuple[CaptureScene, ...], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def require_unique_scene_ids(self) -> Self:
        scene_ids = tuple(scene.scene_id for scene in self.scenes)
        if len(scene_ids) != len(set(scene_ids)):
            error_type = "duplicate_scene_id"
            error_message = "scene_id values must be unique within one capture job"
            raise PydanticCustomError(
                error_type,
                error_message,
            )
        return self


class ErrorCode(StrEnum):
    INPUT_ASSET_MISSING = "input_asset_missing"
    APPIUM_UNAVAILABLE = "appium_unavailable"
    APPIUM_SESSION_FAILED = "appium_session_failed"
    SCENE_CAPTURE_FAILED = "scene_capture_failed"
    LOCK_SCREEN_UNAVAILABLE = "lock_screen_unavailable"
    PHYSICAL_DEVICE_UNAVAILABLE = "physical_device_unavailable"
    COMPOSITION_FAILED = "composition_failed"
    APPIUM_ENDPOINT_REJECTED = "appium_endpoint_rejected"
    CAPTURE_TIMED_OUT = "capture_timed_out"
    CAPTURE_CANCELLED = "capture_cancelled"
    CAPTURE_LEASE_UNAVAILABLE = "capture_lease_unavailable"
    DEVICE_BUSY = "capture_lease_unavailable"
    EXPORT_STALE = "export_stale"
    EXPORT_UNVERIFIED = "export_unverified"
    EXPORT_INVALID = "export_invalid"
    EXPORT_FAILED = "export_failed"


class JobStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class CaptureError(ContractModel):
    code: ErrorCode
    message: Annotated[str, Field(min_length=1, max_length=500)]
    cleanup_error: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class ComponentExportFailure(ContractModel):
    schema_version: Literal["trace.component-export-failure.v1"]
    code: Literal["export_failed"]
    message: Annotated[str, Field(min_length=1, max_length=500)]


Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ComponentExportManifest(ContractModel):
    schema_version: Literal["trace.component-export-manifest.v1"]
    request_sha256: Sha256Digest
    export_nonce: Sha256Digest
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]+$")]
    device_udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    role: Literal["trace_components"]
    artifact_sha256: Sha256Digest
    width: Annotated[int, Field(gt=0, le=8192)]
    height: Annotated[int, Field(gt=0, le=8192)]


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
    source: Literal["native_appium", "offline_fixture"] = "native_appium"
    native_export_nonce: Sha256Digest | None = None
    native_export_binding_verified: bool = False


class CompletedSceneCapture(ContractModel):
    scene_id: Identifier
    status: Literal["completed"]
    image_path: RelativePath
    provenance: CaptureProvenance


class FailedSceneCapture(ContractModel):
    scene_id: Identifier
    status: Literal["failed"]
    error: CaptureError
    evidence_path: RelativePath | None = None


SceneCapture = Annotated[
    CompletedSceneCapture | FailedSceneCapture,
    Field(discriminator="status"),
]


class CaptureResult(ContractModel):
    schema_version: Literal["trace.capture-result.v1"] = "trace.capture-result.v1"
    job_id: Identifier
    status: JobStatus
    captures: tuple[SceneCapture, ...]
    errors: tuple[CaptureError, ...] = ()

    @classmethod
    def from_captures(
        cls,
        job_id: str,
        captures: tuple[CompletedSceneCapture | FailedSceneCapture, ...],
        errors: tuple[CaptureError, ...] = (),
    ) -> Self:
        completed_count = sum(capture.status == "completed" for capture in captures)
        if completed_count == len(captures):
            status = JobStatus.COMPLETED
        elif completed_count == 0:
            status = JobStatus.FAILED
        else:
            status = JobStatus.PARTIAL
        return cls(job_id=job_id, status=status, captures=captures, errors=errors)


class CompositeCanvas(ContractModel):
    width: Annotated[int, Field(ge=320, le=4096)]
    height: Annotated[int, Field(ge=640, le=4096)]


class CompositeLayers(ContractModel):
    background: RelativePath
    trace_components: RelativePath
    iphone_ui: RelativePath | None = None


class MarketingCompositeJob(ContractModel):
    schema_version: Literal["trace.marketing-composite-job.v2"]
    job_id: Identifier
    context: MarketingContext
    canvas: CompositeCanvas
    layers: CompositeLayers
    output_image: RelativePath

    @model_validator(mode="after")
    def require_distinct_layer_paths(self) -> Self:
        paths = tuple(
            path
            for path in (
                self.layers.background,
                self.layers.trace_components,
                self.layers.iphone_ui,
                self.output_image,
            )
            if path is not None
        )
        if len(paths) != len(set(paths)):
            error_type = "composite_path_collision"
            error_message = "composite input and output paths must be distinct"
            raise PydanticCustomError(error_type, error_message)
        return self


class MarketingCompositeResult(ContractModel):
    schema_version: Literal["trace.marketing-composite-result.v2"] = (
        "trace.marketing-composite-result.v2"
    )
    job_id: Identifier
    status: JobStatus
    layers: CompositeLayers
    output_image: RelativePath | None = None
    normalized_iphone_ui: RelativePath | None = None
    errors: tuple[CaptureError, ...] = ()
