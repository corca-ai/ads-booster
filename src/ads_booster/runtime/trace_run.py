from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, assert_never

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.contracts.results import (
    CaptureCompleted,
    CaptureOutcome,
    ComposeCompleted,
    ComposeOutcome,
    ToolFailed,
    TraceRunResult,
)
from ads_booster.contracts.run import (
    TraceRunCapability,
    TraceRunErrorCode,
    TraceRunFailure,
    TraceRunRequest,
    TraceRunState,
)
from ads_booster.runtime.trace_run_artifacts import (
    artifact_matches,
    composition_paths_are_safe,
    safe_artifact,
    safe_job_path,
)
from ads_booster.runtime.trace_run_capture import (
    CapturePort,
    CaptureWorkerPort,
    ComposePort,
    LocalComposePort,
    build_trace_run_result,
)
from ads_booster.runtime.trace_run_store import (
    IdempotencyConflictError,
    JsonlTraceRunStore,
    TraceRunRecord,
)

CAPABILITY_SEQUENCE: Final = (
    TraceRunCapability.CAPTURE,
    TraceRunCapability.STAGE_COMPONENTS,
    TraceRunCapability.COMPOSE,
)


@dataclass(frozen=True, slots=True)
class TraceRunRunner:
    store: JsonlTraceRunStore
    capture_port: CapturePort
    compose_port: ComposePort

    def run(self, request: TraceRunRequest, job_root: Path) -> TraceRunResult:
        record = self.store.begin(request)
        if record.resumed and record.state is TraceRunState.AWAITING_TOOL:
            record = self.store.transition(
                record=record,
                state=TraceRunState.UNKNOWN_SIDE_EFFECT,
                capability=record.awaiting_capability,
            )
        while True:
            match record.state:
                case (
                    TraceRunState.COMPLETED
                    | TraceRunState.FAILED
                    | TraceRunState.ABORTED
                    | TraceRunState.UNKNOWN_SIDE_EFFECT
                ):
                    return build_trace_run_result(record, request, job_root)
                case TraceRunState.QUEUED:
                    record = self.store.transition(record=record, state=TraceRunState.RUNNING)
                case TraceRunState.RUNNING:
                    record = self._await_next_capability(record=record)
                case TraceRunState.AWAITING_TOOL:
                    record = self._run_capability(record=record, request=request, job_root=job_root)
                case _ as unreachable:
                    assert_never(unreachable)

    def _await_next_capability(self, record: TraceRunRecord) -> TraceRunRecord:
        completed = record.completed_capabilities
        if len(completed) >= len(CAPABILITY_SEQUENCE):
            return self._fail(
                record,
                TraceRunErrorCode.COMPOSE_FAILED,
                "run journal contains too many completed capabilities",
            )
        return self.store.transition(
            record=record,
            state=TraceRunState.AWAITING_TOOL,
            capability=CAPABILITY_SEQUENCE[len(completed)],
        )

    def _run_capability(
        self,
        record: TraceRunRecord,
        request: TraceRunRequest,
        job_root: Path,
    ) -> TraceRunRecord:
        capability = record.awaiting_capability
        match capability:
            case TraceRunCapability.CAPTURE:
                outcome = self.capture_port.capture(
                    run_id=request.run_id,
                    job=request.capture_job,
                    job_root=job_root,
                )
                return self._handle_capture(record=record, outcome=outcome)
            case TraceRunCapability.STAGE_COMPONENTS:
                return self._stage_component(record=record, request=request, job_root=job_root)
            case TraceRunCapability.COMPOSE:
                if not composition_paths_are_safe(request.composite_job, job_root):
                    return self._fail(
                        record,
                        TraceRunErrorCode.COMPOSE_FAILED,
                        "composition paths must stay inside the job root",
                    )
                outcome = self.compose_port.compose(
                    run_id=request.run_id,
                    job=request.composite_job,
                    job_root=job_root,
                )
                return self._handle_composition(record, outcome, request, job_root)
            case None:
                return self._fail(
                    record,
                    TraceRunErrorCode.COMPOSE_FAILED,
                    "awaiting tool state has no capability",
                )
            case _ as unreachable:
                assert_never(unreachable)

    def _handle_capture(self, record: TraceRunRecord, outcome: CaptureOutcome) -> TraceRunRecord:
        match outcome:
            case CaptureCompleted(
                component_artifact=component_artifact,
                capture_provenance=capture_provenance,
            ):
                artifact = safe_artifact(component_artifact)
                if artifact is None:
                    return self._fail(
                        record,
                        TraceRunErrorCode.CAPTURE_FAILED,
                        "capture returned an unavailable component artifact",
                    )
                return self.store.transition(
                    record,
                    TraceRunState.RUNNING,
                    component_artifact=artifact,
                    capture_provenance=capture_provenance,
                )
            case ToolFailed(failure=failure):
                return self.store.transition(record, TraceRunState.FAILED, failure=failure)
            case _ as unreachable:
                assert_never(unreachable)

    def _stage_component(
        self,
        record: TraceRunRecord,
        request: TraceRunRequest,
        job_root: Path,
    ) -> TraceRunRecord:
        source = record.captured_artifact
        digest = record.captured_artifact_sha256
        destination = safe_job_path(job_root, request.composite_job.layers.trace_components)
        if source is None or digest is None or destination is None:
            return self._fail(
                record,
                TraceRunErrorCode.STAGE_FAILED,
                "capture artifact or staging path is unavailable",
            )
        if not artifact_matches(source, digest):
            return self._fail(
                record,
                TraceRunErrorCode.STAGE_FAILED,
                "capture artifact digest no longer matches its journal provenance",
            )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(source, destination)
        except OSError as error:
            return self._fail(record, TraceRunErrorCode.STAGE_FAILED, str(error))
        if not artifact_matches(destination, digest):
            return self._fail(
                record,
                TraceRunErrorCode.STAGE_FAILED,
                "staged component artifact failed its digest check",
            )
        return self.store.transition(record, TraceRunState.RUNNING)

    def _handle_composition(
        self,
        record: TraceRunRecord,
        outcome: ComposeOutcome,
        request: TraceRunRequest,
        job_root: Path,
    ) -> TraceRunRecord:
        match outcome:
            case ComposeCompleted(output_image=output_image):
                expected = safe_job_path(job_root, request.composite_job.output_image)
                artifact = safe_artifact(output_image)
                if expected is None or artifact != expected:
                    return self._fail(
                        record,
                        TraceRunErrorCode.COMPOSE_FAILED,
                        "composition returned an unavailable or unexpected output image",
                    )
                return self.store.transition(record, TraceRunState.COMPLETED, output_image=expected)
            case ToolFailed(failure=failure):
                return self.store.transition(record, TraceRunState.FAILED, failure=failure)
            case _ as unreachable:
                assert_never(unreachable)

    def _fail(
        self,
        record: TraceRunRecord,
        code: TraceRunErrorCode,
        message: str,
    ) -> TraceRunRecord:
        return self.store.transition(
            record=record,
            state=TraceRunState.FAILED,
            failure=TraceRunFailure(code=code, message=message),
        )


__all__ = [
    "CaptureCompleted",
    "CapturePort",
    "CaptureWorkerPort",
    "ComposeCompleted",
    "ComposePort",
    "IdempotencyConflictError",
    "LocalComposePort",
    "ToolFailed",
    "TraceRunCapability",
    "TraceRunRequest",
    "TraceRunResult",
    "TraceRunRunner",
    "TraceRunState",
]
