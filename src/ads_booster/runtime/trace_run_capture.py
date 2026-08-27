from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, assert_never

from ads_booster.capture.worker import CaptureExecutionOptions, CaptureWorker, SceneCaptureAdapter
from ads_booster.composition.composite_worker import CompositeWorker
from ads_booster.contracts import (
    CaptureJob,
    CaptureProvenance,
    CompletedSceneCapture,
    FailedSceneCapture,
    JobStatus,
    MarketingCompositeJob,
)
from ads_booster.contracts.results import (
    CaptureCompleted,
    CaptureOutcome,
    ComposeCompleted,
    ComposeOutcome,
    ToolFailed,
    TraceRunResult,
)
from ads_booster.contracts.run import (
    TraceRunErrorCode,
    TraceRunFailure,
    TraceRunRequest,
    TraceRunState,
)
from ads_booster.runtime.trace_run_artifacts import (
    artifact_matches,
    safe_artifact,
    safe_capture_root,
    safe_job_path,
    same_path,
)
from ads_booster.runtime.trace_run_store import ArtifactIntegrityError, TraceRunRecord

if TYPE_CHECKING:
    from pathlib import Path


class CapturePort(Protocol):
    def capture(self, run_id: str, job: CaptureJob, job_root: Path) -> CaptureOutcome: ...


class ComposePort(Protocol):
    def compose(
        self,
        run_id: str,
        job: MarketingCompositeJob,
        job_root: Path,
    ) -> ComposeOutcome: ...


@dataclass(frozen=True, slots=True)
class LocalArtifactCapturePort:
    """Stands in for the native capture on a host that has no capture environment.

    It performs no capture at all: it hands back a packaged component fixture and says so
    in the provenance it writes. `native_export_binding_verified` is False and the source
    is `offline_fixture`, so nothing downstream can mistake this for a device export.
    """

    component_artifact: Path | None

    def capture(self, run_id: str, job: CaptureJob, job_root: Path) -> CaptureOutcome:
        _ = (run_id, job_root)
        artifact = safe_artifact(self.component_artifact)
        if artifact is None:
            return ToolFailed(
                failure=TraceRunFailure(
                    code=TraceRunErrorCode.CAPTURE_FAILED,
                    message="local component artifact is unavailable",
                )
            )
        return CaptureCompleted(
            component_artifact=artifact,
            capture_provenance=_offline_provenance(artifact, job),
        )


@dataclass(frozen=True, slots=True)
class LocalComposePort:
    worker: CompositeWorker = field(default_factory=CompositeWorker)

    def compose(self, run_id: str, job: MarketingCompositeJob, job_root: Path) -> ComposeOutcome:
        _ = run_id
        try:
            result = self.worker.run(job=job, job_root=job_root)
        except OSError as error:
            return ToolFailed(
                failure=TraceRunFailure(
                    code=TraceRunErrorCode.COMPOSE_FAILED,
                    message=f"local composition output could not be written: {error}",
                )
            )
        match result.status:
            case JobStatus.COMPLETED if result.output_image is not None:
                return ComposeCompleted(output_image=job_root / result.output_image)
            case JobStatus.COMPLETED:
                return ToolFailed(
                    failure=TraceRunFailure(
                        code=TraceRunErrorCode.COMPOSE_FAILED,
                        message="completed composition did not return its output path",
                    )
                )
            case JobStatus.PARTIAL | JobStatus.FAILED:
                return ToolFailed(
                    failure=TraceRunFailure(
                        code=TraceRunErrorCode.COMPOSE_FAILED,
                        message="local composition did not complete",
                    )
                )
            case _ as unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class CaptureWorkerPort:
    adapter: SceneCaptureAdapter
    options: CaptureExecutionOptions
    output_root: Path

    def capture(self, run_id: str, job: CaptureJob, job_root: Path) -> CaptureOutcome:
        run_root = safe_capture_root(self.output_root, run_id)
        if run_root is None:
            return _capture_failure("capture run output root is unsafe")
        try:
            result = CaptureWorker(adapter=self.adapter, options=self.options).run(
                job=job,
                input_root=job_root,
                output_root=run_root,
            )
        except OSError as error:
            return _capture_failure(f"capture worker output could not be written: {error}")
        if len(result.captures) != 1:
            return _capture_failure("capture worker did not return exactly one scene result")
        capture = result.captures[0]
        match capture:
            case CompletedSceneCapture(image_path=image_path, provenance=provenance):
                return self._completed_artifact(image_path, provenance, run_root)
            case FailedSceneCapture(error=error):
                return ToolFailed(
                    failure=TraceRunFailure(
                        code=TraceRunErrorCode.CAPTURE_FAILED,
                        message=f"capture failed [{error.code}]: {error.message}"[:500],
                        cleanup_error=(
                            error.cleanup_error[:500] if error.cleanup_error is not None else None
                        ),
                    )
                )
            case _ as unreachable:
                assert_never(unreachable)

    def _completed_artifact(
        self,
        image_path: str,
        provenance: CaptureProvenance,
        output_root: Path,
    ) -> CaptureOutcome:
        try:
            root = output_root.resolve()
        except OSError as error:
            return _capture_failure(f"capture output root could not be resolved: {error}")
        candidate = root / image_path
        if candidate.is_symlink():
            return _capture_failure("capture worker returned a symlinked component artifact")
        artifact = candidate.resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file():
            return _capture_failure("capture worker returned an unavailable component artifact")
        try:
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            return _capture_failure(f"component artifact could not be read: {error}")
        if digest != provenance.artifact_sha256:
            return _capture_failure("component artifact digest does not match capture provenance")
        return CaptureCompleted(component_artifact=artifact, capture_provenance=provenance)


def build_trace_run_result(
    record: TraceRunRecord,
    request: TraceRunRequest,
    job_root: Path,
) -> TraceRunResult:
    completed = record.state is TraceRunState.COMPLETED
    component = (
        safe_job_path(job_root, request.composite_job.layers.trace_components)
        if completed
        else None
    )
    output = safe_job_path(job_root, request.composite_job.output_image) if completed else None
    output_event = record.events[-1]
    if record.state is TraceRunState.COMPLETED and (
        component is None
        or record.captured_artifact_sha256 is None
        or not artifact_matches(component, record.captured_artifact_sha256)
        or output is None
        or output_event.output_image is None
        or output_event.output_image_sha256 is None
        or not same_path(output_event.output_image, output)
        or not artifact_matches(output, output_event.output_image_sha256)
    ):
        raise ArtifactIntegrityError(
            path=str(output or component),
            reason="completed run artifacts no longer match journal provenance",
        )
    root = job_root.resolve()
    return TraceRunResult(
        run_id=request.run_id,
        idempotency_key=request.idempotency_key,
        input_digest=record.input_digest,
        state=record.state,
        component_artifact=component.relative_to(root).as_posix()
        if component is not None and component.is_file()
        else None,
        component_artifact_sha256=record.captured_artifact_sha256 if completed else None,
        output_image=output.relative_to(root).as_posix()
        if output is not None and output.is_file()
        else None,
        output_image_sha256=output_event.output_image_sha256 if completed else None,
        capture_provenance=record.capture_provenance if completed else None,
        failure=record.failure,
    )


def _offline_provenance(path: Path, job: CaptureJob) -> CaptureProvenance:
    stat = path.stat()
    content = path.read_bytes()
    return CaptureProvenance(
        request_sha256=hashlib.sha256(job.model_dump_json().encode()).hexdigest(),
        artifact_sha256=hashlib.sha256(content).hexdigest(),
        bundle_id="offline.fixture",
        device_udid=job.device.udid,
        session_id="offline-fixture",
        byte_size=len(content),
        width=1,
        height=1,
        source_modified_at_ns=max(1, stat.st_mtime_ns),
        source="offline_fixture",
        native_export_nonce=None,
        native_export_binding_verified=False,
    )


def _capture_failure(message: str) -> ToolFailed:
    return ToolFailed(
        failure=TraceRunFailure(code=TraceRunErrorCode.CAPTURE_FAILED, message=message)
    )


__all__ = [
    "CapturePort",
    "CaptureWorkerPort",
    "ComposePort",
    "LocalArtifactCapturePort",
    "LocalComposePort",
    "artifact_matches",
    "build_trace_run_result",
    "safe_artifact",
    "safe_job_path",
]
