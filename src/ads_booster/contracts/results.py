from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, LiteralString, assert_never

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import (
    CaptureProvenance,
    ContractModel,
    Identifier,
    Sha256Digest,
)
from ads_booster.contracts.run import TraceRunFailure, TraceRunState

if TYPE_CHECKING:
    from pathlib import Path


class TraceRunResult(ContractModel):
    schema_version: Literal["trace.run-result.v1", "trace.run-result.v2"] = "trace.run-result.v1"
    run_id: Identifier
    idempotency_key: Identifier
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: TraceRunState
    component_artifact: str | None = None
    component_artifact_sha256: Sha256Digest | None = None
    output_image: str | None = None
    output_image_sha256: Sha256Digest | None = None
    capture_provenance: CaptureProvenance | None = None
    failure: TraceRunFailure | None = None

    @model_validator(mode="after")
    def require_state_specific_artifacts(self) -> TraceRunResult:
        v1_artifact_values = (
            self.component_artifact,
            self.component_artifact_sha256,
            self.output_image,
            self.output_image_sha256,
        )
        v2_artifact_values = (self.output_image, self.output_image_sha256)
        match self.state:
            case TraceRunState.COMPLETED:
                if self.schema_version == "trace.run-result.v1" and any(
                    value is None for value in v1_artifact_values
                ):
                    error = _validation_error(
                        "missing_result_artifacts",
                        "completed results require complete artifact claims",
                    )
                    raise error
                if self.schema_version == "trace.run-result.v2" and (
                    any(value is None for value in v2_artifact_values)
                    or self.component_artifact is not None
                    or self.component_artifact_sha256 is not None
                    or self.capture_provenance is None
                    or self.capture_provenance.artifact_role != "trace_wallpaper"
                    or not self.capture_provenance.native_export_binding_verified
                    or self.capture_provenance.artifact_sha256 != self.output_image_sha256
                ):
                    error = _validation_error(
                        "missing_wallpaper_artifact",
                        "completed v2 results require one verified wallpaper artifact",
                    )
                    raise error
            case (
                TraceRunState.FAILED
                | TraceRunState.ABORTED
                | TraceRunState.UNKNOWN_SIDE_EFFECT
                | TraceRunState.QUEUED
                | TraceRunState.RUNNING
                | TraceRunState.AWAITING_TOOL
            ):
                if any(value is not None for value in v1_artifact_values) or (
                    self.capture_provenance is not None
                ):
                    error = _validation_error(
                        "unexpected_result_artifacts",
                        "non-completed results cannot claim artifacts",
                    )
                    raise error
            case _ as unreachable:
                assert_never(unreachable)
        return self


@dataclass(frozen=True, slots=True)
class CaptureCompleted:
    component_artifact: Path
    capture_provenance: CaptureProvenance | None = None


@dataclass(frozen=True, slots=True)
class ComposeCompleted:
    output_image: Path


@dataclass(frozen=True, slots=True)
class ToolFailed:
    failure: TraceRunFailure


CaptureOutcome = CaptureCompleted | ToolFailed
ComposeOutcome = ComposeCompleted | ToolFailed


def _validation_error(code: LiteralString, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(code, message)
