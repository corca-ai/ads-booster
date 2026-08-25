from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, LiteralString, assert_never

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from trace_capture.contracts.models import (
    CaptureJob,
    CaptureProvenance,
    ContractModel,
    Identifier,
    MarketingCompositeJob,
    Sha256Digest,
)


class TraceRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_TOOL = "awaiting_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class TraceRunCapability(StrEnum):
    CAPTURE = "capture"
    STAGE_COMPONENTS = "stage_components"
    COMPOSE = "compose"


class TraceRunErrorCode(StrEnum):
    CAPTURE_FAILED = "capture_failed"
    STAGE_FAILED = "stage_failed"
    COMPOSE_FAILED = "compose_failed"


class TraceRunFailure(ContractModel):
    code: TraceRunErrorCode
    message: str = Field(min_length=1, max_length=500)
    cleanup_error: str | None = Field(default=None, min_length=1, max_length=500)


class TraceRunRequest(ContractModel):
    schema_version: Literal["trace.run-job.v1"] = "trace.run-job.v1"
    run_id: Identifier
    idempotency_key: Identifier
    capture_job: CaptureJob
    composite_job: MarketingCompositeJob

    @model_validator(mode="after")
    def require_one_scene_and_matching_context(self) -> TraceRunRequest:
        if len(self.capture_job.scenes) != 1:
            error = PydanticCustomError(
                "trace_run_scene_count",
                "a trace run must contain exactly one capture scene",
            )
            raise error
        if self.capture_job.context != self.composite_job.context:
            error = PydanticCustomError(
                "trace_run_context_mismatch",
                "capture and composite contexts must match",
            )
            raise error
        return self


class TraceRunEvent(ContractModel):
    schema_version: Literal["trace.run-event.v1"] = "trace.run-event.v1"
    run_id: Identifier
    idempotency_key: Identifier
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=0)
    recorded_at: datetime
    state: TraceRunState
    capability: TraceRunCapability | None = None
    failure: TraceRunFailure | None = None
    component_artifact: str | None = Field(default=None, min_length=1, max_length=1024)
    component_artifact_sha256: Sha256Digest | None = None
    output_image: str | None = Field(default=None, min_length=1, max_length=1024)
    output_image_sha256: Sha256Digest | None = None
    capture_provenance: CaptureProvenance | None = None

    @model_validator(mode="after")
    def require_state_specific_fields(self) -> TraceRunEvent:
        _validate_utc_timestamp(self)
        match self.state:
            case TraceRunState.AWAITING_TOOL:
                _validate_awaiting_event(self)
            case TraceRunState.FAILED:
                _validate_failed_event(self)
            case TraceRunState.QUEUED | TraceRunState.ABORTED:
                _validate_idle_event(self)
            case TraceRunState.COMPLETED:
                _validate_completed_event(self)
            case TraceRunState.UNKNOWN_SIDE_EFFECT:
                _validate_unknown_side_effect_event(self)
            case TraceRunState.RUNNING:
                _validate_running_event(self)
            case _ as unreachable:
                assert_never(unreachable)
        return self


def _validation_error(code: LiteralString, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(code, message)


def _require_no_capture_provenance(event: TraceRunEvent) -> None:
    if event.capture_provenance is not None:
        error = _validation_error(
            "unexpected_capture_provenance",
            "this run event cannot contain capture provenance",
        )
        raise error


def _validate_awaiting_event(event: TraceRunEvent) -> None:
    _require_no_capture_provenance(event)
    if event.capability is None:
        error = _validation_error("missing_capability", "awaiting_tool events require a capability")
        raise error
    if event.failure is not None:
        error = _validation_error(
            "unexpected_failure", "awaiting_tool events cannot contain a failure"
        )
        raise error
    if (
        event.component_artifact is not None
        or event.component_artifact_sha256 is not None
        or event.output_image is not None
        or event.output_image_sha256 is not None
    ):
        error = _validation_error(
            "unexpected_event_data",
            "awaiting_tool events cannot contain artifact data",
        )
        raise error


def _validate_failed_event(event: TraceRunEvent) -> None:
    _require_no_capture_provenance(event)
    if event.failure is None:
        error = _validation_error("missing_failure", "failed events require a failure")
        raise error
    if event.capability is not None:
        error = _validation_error(
            "unexpected_capability", "failed events cannot contain a capability"
        )
        raise error
    if (
        event.component_artifact is not None
        or event.component_artifact_sha256 is not None
        or event.output_image is not None
        or event.output_image_sha256 is not None
    ):
        error = _validation_error(
            "unexpected_event_data",
            "failed events cannot contain artifact data",
        )
        raise error


def _validate_idle_event(event: TraceRunEvent) -> None:
    _require_no_capture_provenance(event)
    if event.capability is not None or event.failure is not None:
        error = _validation_error(
            "unexpected_event_data",
            "this run state cannot contain a capability or failure",
        )
        raise error
    if (
        event.component_artifact is not None
        or event.component_artifact_sha256 is not None
        or event.output_image is not None
        or event.output_image_sha256 is not None
    ):
        error = _validation_error(
            "unexpected_event_data",
            "this run state cannot contain artifact data",
        )
        raise error


def _validate_running_event(event: TraceRunEvent) -> None:
    if event.capability is not None or event.failure is not None:
        error = _validation_error(
            "unexpected_event_data",
            "running events cannot contain a capability or failure",
        )
        raise error
    if event.output_image is not None or event.output_image_sha256 is not None:
        error = _validation_error(
            "unexpected_output_image",
            "running events cannot contain an output image",
        )
        raise error
    if event.component_artifact is None and event.component_artifact_sha256 is not None:
        error = _validation_error(
            "orphan_artifact_digest",
            "component artifact digests require a component artifact",
        )
        raise error
    if event.component_artifact is not None and event.component_artifact_sha256 is None:
        error = _validation_error(
            "missing_artifact_digest",
            "component artifacts require a SHA-256 digest",
        )
        raise error


def _validate_completed_event(event: TraceRunEvent) -> None:
    _require_no_capture_provenance(event)
    if event.capability is not None or event.failure is not None:
        error = _validation_error(
            "unexpected_event_data",
            "completed events cannot contain a capability or failure",
        )
        raise error
    if event.component_artifact is not None or event.component_artifact_sha256 is not None:
        error = _validation_error(
            "unexpected_component_artifact",
            "completed events cannot contain a component artifact",
        )
        raise error
    if event.output_image is None or event.output_image_sha256 is None:
        error = _validation_error(
            "missing_output_provenance",
            "completed events require an output image and SHA-256 digest",
        )
        raise error


def _validate_unknown_side_effect_event(event: TraceRunEvent) -> None:
    _require_no_capture_provenance(event)
    if event.capability is None:
        error = _validation_error(
            "missing_capability",
            "unknown side-effect events require the unresolved capability",
        )
        raise error
    if event.failure is not None:
        error = _validation_error(
            "unexpected_failure",
            "unknown side-effect events cannot contain a failure",
        )
        raise error
    if (
        event.component_artifact is not None
        or event.component_artifact_sha256 is not None
        or event.output_image is not None
        or event.output_image_sha256 is not None
    ):
        error = _validation_error(
            "unexpected_event_data",
            "unknown side-effect events cannot contain artifact data",
        )
        raise error


def _validate_utc_timestamp(event: TraceRunEvent) -> None:
    offset = event.recorded_at.utcoffset()
    if event.recorded_at.tzinfo is None or offset != timedelta():
        error = _validation_error("non_utc_timestamp", "run events require a UTC timestamp")
        raise error
