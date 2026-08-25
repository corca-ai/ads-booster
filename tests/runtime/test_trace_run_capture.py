from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass, fields
from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

import pytest

from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.capture.worker import CaptureExecutionOptions, CaptureRequest
from trace_capture.contracts import CaptureJob, CaptureProvenance, ErrorCode
from trace_capture.runtime.trace_run import CaptureCompleted, ToolFailed
from trace_capture.runtime.trace_run_capture import CaptureWorkerPort

from .test_trace_run import CAPTURE_JSON

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProvenanceAdapter:
    content: bytes

    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        _ = request.destination.write_bytes(self.content)
        return CaptureProvenance(
            request_sha256="a" * 64,
            artifact_sha256=sha256(self.content).hexdigest(),
            bundle_id="com.corca.Trace",
            device_udid=request.device.udid,
            session_id="session-01",
            byte_size=len(self.content),
            width=320,
            height=640,
            source_modified_at_ns=1,
            native_export_nonce="b" * 64,
            native_export_binding_verified=True,
        )


@dataclass(frozen=True, slots=True)
class FailingAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(code=ErrorCode.EXPORT_INVALID, message="invalid export")


@dataclass(frozen=True, slots=True)
class CleanupFailingAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="invalid export",
            cleanup_error="session cleanup failed",
        )


def test_capture_port_when_worker_completes_then_it_returns_the_capture_output_artifact(
    tmp_path: Path,
) -> None:
    # Given a one-scene CaptureJob and an adapter that returns validated provenance
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    output_root = tmp_path / "capture-output"
    port = CaptureWorkerPort(
        adapter=ProvenanceAdapter(content=b"component-png"),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=output_root,
    )

    # When TraceRun calls the capture port
    outcome = port.capture(run_id="run-01", job=job, job_root=tmp_path / "inputs")

    # Then it returns the provenance-bound artifact inside CaptureWorker output
    expected = output_root / "run-01" / "capture-01" / "scene-01.png"
    match outcome:
        case CaptureCompleted(component_artifact=artifact):
            assert artifact == expected
            assert artifact.read_bytes() == b"component-png"
        case ToolFailed(failure=failure):
            pytest.fail(f"unexpected capture failure: {failure}")
        case _ as unreachable:
            assert_never(unreachable)


def test_capture_port_when_native_manifest_is_verified_then_it_marks_native_provenance(
    tmp_path: Path,
) -> None:
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    port = CaptureWorkerPort(
        adapter=ProvenanceAdapter(content=b"component-png"),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=tmp_path / "capture-output",
    )

    outcome = port.capture(run_id="run-native", job=job, job_root=tmp_path / "inputs")

    match outcome:
        case CaptureCompleted(component_artifact=artifact):
            assert "capture_provenance" in {field.name for field in fields(outcome)}
            assert artifact.is_file()
        case ToolFailed(failure=failure):
            pytest.fail(f"unexpected capture failure: {failure}")
        case _ as unreachable:
            assert_never(unreachable)


def test_capture_port_when_worker_fails_then_it_maps_the_failed_scene_to_tool_failure(
    tmp_path: Path,
) -> None:
    # Given an adapter that reports a typed capture failure
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    port = CaptureWorkerPort(
        adapter=FailingAdapter(),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=tmp_path / "capture-output",
    )

    # When TraceRun invokes the port
    outcome = port.capture(run_id="run-01", job=job, job_root=tmp_path / "inputs")

    # Then the failed scene is converted into a capture tool failure
    match outcome:
        case CaptureCompleted():
            pytest.fail("expected capture failure")
        case ToolFailed(failure=failure):
            assert failure.code.value == "capture_failed"
            assert "invalid export" in failure.message
        case _ as unreachable:
            assert_never(unreachable)


def test_capture_port_when_cleanup_also_fails_then_it_preserves_bounded_cleanup_evidence(
    tmp_path: Path,
) -> None:
    # Given an adapter whose primary capture failure also carries cleanup evidence
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    port = CaptureWorkerPort(
        adapter=CleanupFailingAdapter(),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=tmp_path / "capture-output",
    )

    # When TraceRun invokes the port
    outcome = port.capture(run_id="run-01", job=job, job_root=tmp_path / "inputs")

    # Then the primary failure and bounded cleanup evidence survive the mapping
    match outcome:
        case CaptureCompleted():
            pytest.fail("expected capture failure")
        case ToolFailed(failure=failure):
            assert failure.message == "capture failed [export_invalid]: invalid export"
            assert failure.cleanup_error == "session cleanup failed"
            assert len(failure.cleanup_error) <= 500
        case _ as unreachable:
            assert_never(unreachable)


def test_capture_port_when_run_ids_share_a_job_then_outputs_remain_isolated(
    tmp_path: Path,
) -> None:
    # Given two Trace runs that use the same capture job identity
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    port = CaptureWorkerPort(
        adapter=ProvenanceAdapter(content=b"component-png"),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=tmp_path / "capture-output",
    )

    # When each run invokes the capture worker
    first = port.capture(run_id="run-01", job=job, job_root=tmp_path / "inputs")
    second = port.capture(run_id="run-02", job=job, job_root=tmp_path / "inputs")

    # Then each run receives an artifact under its own output namespace
    match first, second:
        case CaptureCompleted(component_artifact=first_path), CaptureCompleted(
            component_artifact=second_path
        ):
            assert first_path != second_path
            expected_first = tmp_path / "capture-output" / "run-01" / "capture-01" / "scene-01.png"
            expected_second = tmp_path / "capture-output" / "run-02" / "capture-01" / "scene-01.png"
            assert first_path == expected_first
            assert second_path == expected_second
        case ToolFailed(), _:
            pytest.fail("first capture did not complete")
        case _, ToolFailed():
            pytest.fail("second capture did not complete")
        case _ as unreachable:
            assert_never(unreachable)


def test_capture_port_when_output_root_is_a_symlink_then_it_fails_closed(
    tmp_path: Path,
) -> None:
    # Given a capture output root that redirects through a symlink
    job = CaptureJob.model_validate_json(CAPTURE_JSON)
    background = tmp_path / "inputs" / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"background")
    outside = tmp_path / "outside"
    outside.mkdir()
    output_root = tmp_path / "capture-output"
    output_root.symlink_to(outside, target_is_directory=True)
    port = CaptureWorkerPort(
        adapter=ProvenanceAdapter(content=b"component-png"),
        options=CaptureExecutionOptions(timeout_seconds=30),
        output_root=output_root,
    )

    # When TraceRun invokes the capture port
    outcome = port.capture(run_id="run-01", job=job, job_root=tmp_path / "inputs")

    # Then no capture artifact is written through the symlink
    match outcome:
        case CaptureCompleted():
            pytest.fail("expected capture failure")
        case ToolFailed(failure=failure):
            assert failure.code.value == "capture_failed"
            assert "output root" in failure.message
        case _ as unreachable:
            assert_never(unreachable)
    assert not (outside / "run-01").exists()
