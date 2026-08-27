from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.contracts import CaptureJob, MarketingCompositeJob
from ads_booster.contracts.run import TraceRunErrorCode, TraceRunEvent, TraceRunFailure
from ads_booster.runtime.trace_run import (
    CaptureCompleted,
    ComposeCompleted,
    ToolFailed,
    TraceRunCapability,
    TraceRunRequest,
    TraceRunResult,
    TraceRunRunner,
    TraceRunState,
)
from ads_booster.runtime.trace_run_store import JsonlTraceRunStore

if TYPE_CHECKING:
    from pathlib import Path


CAPTURE_JSON = """
{
  "schema_version": "trace.capture-job.v1",
  "job_id": "capture-01",
  "context": {
    "country": "JP",
    "persona_id": "student",
    "promotion_material_id": "exam"
  },
  "device": {
    "kind": "simulator",
    "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
    "platform_version": "26.5",
    "device_name": "iPhone 17 Pro"
  },
  "scenes": [{
    "scene_id": "scene-01",
    "locale": "ja-JP",
    "capture_target": "trace_components",
    "background_image": "inputs/background.png",
    "trace_data": {"rows": [{"layout": "one_by_one", "components": [{
      "title": "Dynamic card", "items": ["A", "B", "C"]
    }]}]}
  }]
}
"""

COMPOSE_JSON = """
{
  "schema_version": "trace.marketing-composite-job.v2",
  "job_id": "compose-01",
  "context": {
    "country": "JP",
    "persona_id": "student",
    "promotion_material_id": "exam"
  },
  "canvas": {"width": 320, "height": 640},
  "layers": {
    "background": "inputs/background.png",
    "trace_components": "work/trace-components.png",
    "iphone_ui": "inputs/iphone-ui.png"
  },
  "output_image": "outputs/final.png"
}
"""


def make_request(run_id: str = "run-01") -> TraceRunRequest:
    return TraceRunRequest(
        schema_version="trace.run-job.v1",
        run_id=run_id,
        idempotency_key=f"{run_id}-key",
        capture_job=CaptureJob.model_validate_json(CAPTURE_JSON),
        composite_job=MarketingCompositeJob.model_validate_json(COMPOSE_JSON),
    )


class CallAccumulator:
    """Mutable test-only sink; recording calls is its sole purpose."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, run_id: str) -> None:
        self.calls.append(run_id)


@dataclass(frozen=True, slots=True)
class RecordingCapturePort:
    source: Path
    call_log: CallAccumulator

    def capture(self, run_id: str, job: CaptureJob, job_root: Path) -> CaptureCompleted:
        _ = (job, job_root)
        self.call_log.record(run_id)
        return CaptureCompleted(component_artifact=self.source)


@dataclass(frozen=True, slots=True)
class RecordingComposePort:
    call_log: CallAccumulator

    def compose(self, run_id: str, job: MarketingCompositeJob, job_root: Path) -> ComposeCompleted:
        self.call_log.record(run_id)
        output = job_root / job.output_image
        output.parent.mkdir(parents=True, exist_ok=True)
        _ = output.write_bytes(b"composed")
        _ = job
        return ComposeCompleted(output_image=output)


def test_runner_when_first_run_completes_then_replay_is_idempotent(tmp_path: Path) -> None:
    # Given a one-scene request and injected capture and compose ports
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()
    capture = RecordingCapturePort(source=component, call_log=capture_log)
    compose = RecordingComposePort(call_log=compose_log)
    store = JsonlTraceRunStore(root=tmp_path / "state")
    runner = TraceRunRunner(store=store, capture_port=capture, compose_port=compose)

    # When the same request is run twice after the first completed run
    first = runner.run(request=make_request(), job_root=tmp_path)
    second = runner.run(request=make_request(), job_root=tmp_path)

    # Then both tools ran only once and the staged artifact is the declared compose layer
    assert first.state is TraceRunState.COMPLETED
    assert second.state is TraceRunState.COMPLETED
    assert capture_log.calls == ["run-01"]
    assert compose_log.calls == ["run-01"]
    assert (tmp_path / "work" / "trace-components.png").read_bytes() == b"component"
    journal = (tmp_path / "state" / "run-01" / "transitions.jsonl").read_text(encoding="utf-8")
    assert '"state":"queued"' in journal
    assert '"state":"completed"' in journal
    events = [TraceRunEvent.model_validate_json(line) for line in journal.splitlines()]
    assert [event.sequence for event in events] == list(range(len(events)))
    assert {event.idempotency_key for event in events} == {"run-01-key"}
    assert {event.recorded_at.utcoffset() for event in events} == {timedelta()}


def test_runner_when_restarted_while_awaiting_capture_then_replays_from_journal(
    tmp_path: Path,
) -> None:
    # Given a process stopped after it durably recorded the capture capability request
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    record = store.begin(request)
    running = store.transition(record=record, state=TraceRunState.RUNNING)
    _ = store.transition(
        record=running,
        state=TraceRunState.AWAITING_TOOL,
        capability=TraceRunCapability.CAPTURE,
    )
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()
    capture = RecordingCapturePort(source=component, call_log=capture_log)
    compose = RecordingComposePort(call_log=compose_log)

    # When a new runner instance receives the same request
    result = TraceRunRunner(store=store, capture_port=capture, compose_port=compose).run(
        request=request,
        job_root=tmp_path,
    )

    # Then replay converts the unresolved capability to a fail-closed state
    assert result.state.value == "unknown_side_effect"
    assert capture_log.calls == []
    assert compose_log.calls == []


def test_runner_when_resumed_with_an_unresolved_tool_then_it_fails_closed_without_replay(
    tmp_path: Path,
) -> None:
    # Given a journal that durably requested capture before the process disappeared
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    queued = store.begin(request)
    running = store.transition(record=queued, state=TraceRunState.RUNNING)
    _ = store.transition(
        record=running,
        state=TraceRunState.AWAITING_TOOL,
        capability=TraceRunCapability.CAPTURE,
    )
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()

    # When a fresh runner resumes that journal
    result = TraceRunRunner(
        store=store,
        capture_port=RecordingCapturePort(source=component, call_log=capture_log),
        compose_port=RecordingComposePort(call_log=compose_log),
    ).run(request=request, job_root=tmp_path)

    # Then it records an operator-reconciliation state and calls no external capability
    assert result.state.value == "unknown_side_effect"
    assert capture_log.calls == []
    assert compose_log.calls == []


@dataclass(frozen=True, slots=True)
class CleanupFailureCapturePort:
    def capture(self, run_id: str, job: CaptureJob, job_root: Path) -> ToolFailed:
        _ = (run_id, job, job_root)
        return ToolFailed(
            failure=TraceRunFailure(
                code=TraceRunErrorCode.CAPTURE_FAILED,
                message="capture failed",
                cleanup_error="session cleanup failed",
            )
        )


def test_runner_when_capture_cleanup_fails_then_event_and_result_retain_evidence(
    tmp_path: Path,
) -> None:
    # Given a capture capability that returns primary and cleanup failure evidence
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")

    # When the run reaches the capture boundary
    result = TraceRunRunner(
        store=store,
        capture_port=CleanupFailureCapturePort(),
        compose_port=RecordingComposePort(call_log=CallAccumulator()),
    ).run(request=request, job_root=tmp_path)

    # Then the returned result and durable failed event retain bounded cleanup evidence
    assert result.failure is not None
    assert result.failure.cleanup_error == "session cleanup failed"
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    events = [TraceRunEvent.model_validate_json(line) for line in journal.read_text().splitlines()]
    assert events[-1].failure is not None
    assert events[-1].failure.cleanup_error == "session cleanup failed"


def test_runner_when_aborted_then_it_never_calls_a_capability(tmp_path: Path) -> None:
    # Given a durable aborted run
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    record = store.begin(request)
    _ = store.transition(record=record, state=TraceRunState.ABORTED)
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()
    capture = RecordingCapturePort(source=component, call_log=capture_log)
    compose = RecordingComposePort(call_log=compose_log)

    # When the agent retries the aborted identity
    result = TraceRunRunner(store=store, capture_port=capture, compose_port=compose).run(
        request=request,
        job_root=tmp_path,
    )

    # Then the persisted terminal state forbids all future capability calls
    assert result.state is TraceRunState.ABORTED
    assert capture_log.calls == []
    assert compose_log.calls == []


def test_trace_run_result_when_failed_claims_artifacts_then_validation_rejects() -> None:
    # Given a failed result that attempts to claim component and output artifacts
    # When the public result contract is parsed
    with pytest.raises(ValidationError, match="non-completed results cannot claim artifacts"):
        _ = TraceRunResult(
            run_id="run-failed-artifacts",
            idempotency_key="run-failed-artifacts-key",
            input_digest="a" * 64,
            state=TraceRunState.FAILED,
            component_artifact="work/components.png",
            component_artifact_sha256="b" * 64,
            output_image="outputs/final.png",
            output_image_sha256="c" * 64,
            failure=TraceRunFailure(
                code=TraceRunErrorCode.CAPTURE_FAILED,
                message="capture failed",
            ),
        )
