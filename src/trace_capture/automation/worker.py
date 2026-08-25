from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, assert_never

from trace_capture.automation.models import QueueCompletion, QueueState
from trace_capture.contracts.run import TraceRunState

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from trace_capture.automation.models import QueueRecord
    from trace_capture.automation.store import AutomationQueue
    from trace_capture.contracts.generation import MarketingContextBundle
    from trace_capture.contracts.results import TraceRunResult


class GenerateOnePort(Protocol):
    def run(self, bundle: MarketingContextBundle) -> TraceRunResult: ...


@dataclass(frozen=True, slots=True)
class GenerateOneWorker:
    queue: AutomationQueue
    runner: GenerateOnePort
    artifact_root: Path

    def run_claim(self, claimed: QueueRecord, *, now: datetime) -> QueueRecord:
        if claimed.worker_id is None:
            return self.queue.finish(
                claimed,
                completion=QueueCompletion(
                    state=QueueState.FAILED,
                    failure_code="claim_owner_missing",
                ),
                now=now,
            )
        running = self.queue.start(
            claimed.queue_id,
            worker_id=claimed.worker_id,
            expected_revision=claimed.revision,
            now=now,
        )
        result = self.runner.run(running.bundle)
        return self._record_result(running, result, now)

    def _record_result(
        self, running: QueueRecord, result: TraceRunResult, now: datetime
    ) -> QueueRecord:
        match result.state:
            case TraceRunState.COMPLETED:
                return self._record_completed(running, result, now)
            case TraceRunState.UNKNOWN_SIDE_EFFECT:
                failure_code = "unknown_side_effect"
            case TraceRunState.FAILED:
                failure_code = "trace_run_failed"
            case TraceRunState.ABORTED:
                failure_code = "trace_run_aborted"
            case TraceRunState.QUEUED | TraceRunState.RUNNING | TraceRunState.AWAITING_TOOL:
                failure_code = "trace_run_incomplete"
            case _ as unreachable:
                assert_never(unreachable)
        return self.queue.finish(
            running,
            completion=QueueCompletion(
                state=QueueState.FAILED,
                run_id=result.run_id,
                run_idempotency_key=result.idempotency_key,
                failure_code=failure_code,
            ),
            now=now,
        )

    def _record_completed(
        self, running: QueueRecord, result: TraceRunResult, now: datetime
    ) -> QueueRecord:
        if (
            result.run_id != running.bundle.request_id
            or result.idempotency_key != f"{running.bundle.request_id}-v1"
        ):
            return self.queue.finish(
                running,
                completion=QueueCompletion(
                    state=QueueState.FAILED,
                    run_id=result.run_id,
                    run_idempotency_key=result.idempotency_key,
                    failure_code="result_identity_mismatch",
                ),
                now=now,
            )
        artifact_path = result.output_image
        artifact_digest = result.output_image_sha256
        if artifact_path is None or artifact_digest is None:
            return self._artifact_failure(running, result, now)
        job_root = (self.artifact_root / running.bundle.request_id).resolve()
        artifact = (job_root / artifact_path).resolve()
        if (
            not artifact.is_relative_to(job_root)
            or not artifact.is_file()
            or sha256(artifact.read_bytes()).hexdigest() != artifact_digest
        ):
            return self._artifact_failure(running, result, now)
        return self.queue.finish(
            running,
            completion=QueueCompletion(
                state=QueueState.REVIEW,
                run_id=result.run_id,
                run_idempotency_key=result.idempotency_key,
                artifact_path=artifact_path,
                artifact_sha256=artifact_digest,
            ),
            now=now,
        )

    def _artifact_failure(
        self, running: QueueRecord, result: TraceRunResult, now: datetime
    ) -> QueueRecord:
        return self.queue.finish(
            running,
            completion=QueueCompletion(
                state=QueueState.FAILED,
                run_id=result.run_id,
                run_idempotency_key=result.idempotency_key,
                failure_code="artifact_unverified",
            ),
            now=now,
        )
