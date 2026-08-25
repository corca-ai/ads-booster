from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.capture.worker import CaptureRequest, CaptureWorker
from trace_capture.contracts import CaptureJob, CaptureProvenance, ErrorCode, JobStatus

if TYPE_CHECKING:
    from pathlib import Path


JOB_JSON = """
{
  "schema_version": "trace.capture-job.v1",
  "job_id": "job-01",
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
  "scenes": [
    {
      "scene_id": "lockscreen-01",
      "locale": "ja-JP",
      "capture_target": "trace_components",
      "background_image": "backgrounds/exam.jpg",
      "trace_data": {"items": ["試験", "レポート", "夕食"]}
    }
  ]
}
"""


@dataclass(frozen=True, slots=True)
class SuccessfulAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        content = b"captured-png"
        _ = request.destination.write_bytes(content)
        return CaptureProvenance(
            request_sha256="a" * 64,
            artifact_sha256=sha256(content).hexdigest(),
            bundle_id="com.corca.Trace",
            device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            session_id="appium-session-01",
            byte_size=len(content),
            width=1290,
            height=2796,
            source_modified_at_ns=1,
        )


@dataclass(frozen=True, slots=True)
class DiagnosticFailureAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        _ = request.destination.write_bytes(b"diagnostic-png")
        raise CaptureAdapterError(
            code=ErrorCode.LOCK_SCREEN_UNAVAILABLE,
            message="custom wallpaper unavailable",
        )


@dataclass(frozen=True, slots=True)
class PrimaryAndCleanupFailureAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="invalid component export",
            cleanup_error="Appium session cleanup failed",
        )


@dataclass(frozen=True, slots=True)
class OversizedFailureAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="x" * 501,
            cleanup_error="y" * 501,
        )


class NonceRecordingAdapter:
    nonces: list[str]
    backgrounds: list[Path | None]

    def __init__(self) -> None:
        self.nonces = []
        self.backgrounds = []

    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        self.nonces.append(request.capture_nonce)
        self.backgrounds.append(request.background)
        return SuccessfulAdapter().capture(request)


def test_worker_when_capture_succeeds(tmp_path: Path) -> None:
    # Given a validated job and a caller-provided background image
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    output_root = tmp_path / "outputs"
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the independent worker runs the scene adapter
    result = CaptureWorker(adapter=SuccessfulAdapter()).run(
        job=job,
        input_root=input_root,
        output_root=output_root,
    )

    # Then it persists a PNG and a versioned completed result
    assert result.status is JobStatus.COMPLETED
    assert (output_root / "job-01" / "lockscreen-01.png").read_bytes() == b"captured-png"
    persisted = (output_root / "job-01" / "capture-result.json").read_text()
    assert CaptureJob.__name__ not in persisted
    assert '"schema_version":"trace.capture-result.v1"' in persisted
    capture = result.captures[0]
    assert capture.status == "completed"
    assert capture.provenance.session_id == "appium-session-01"
    assert capture.provenance.artifact_sha256 == sha256(b"captured-png").hexdigest()
    assert capture.provenance.bundle_id == "com.corca.Trace"
    assert capture.provenance.device_udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"


def test_worker_when_capture_starts_generates_a_fresh_nonce(tmp_path: Path) -> None:
    # Given one valid source image and a nonce-recording adapter
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    job = CaptureJob.model_validate_json(JOB_JSON)
    adapter = NonceRecordingAdapter()

    # When two independent captures start
    for index in range(2):
        _ = CaptureWorker(adapter=adapter).run(
            job=job,
            input_root=input_root,
            output_root=tmp_path / f"outputs-{index}",
        )

    # Then each capture carries a random 256-bit export nonce
    assert len(adapter.nonces) == 2
    assert adapter.nonces[0] != adapter.nonces[1]
    assert all(
        len(nonce) == 64 and all(char in "0123456789abcdef" for char in nonce)
        for nonce in adapter.nonces
    )


def test_worker_when_background_is_missing(tmp_path: Path) -> None:
    # Given a valid job whose declared source image does not exist
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker prepares the scene
    result = CaptureWorker(adapter=SuccessfulAdapter()).run(
        job=job,
        input_root=tmp_path / "inputs",
        output_root=tmp_path / "outputs",
    )

    # Then it fails closed before opening an Appium capture
    assert result.status is JobStatus.FAILED
    failure = result.captures[0]
    assert failure.status == "failed"
    assert failure.error.code is ErrorCode.INPUT_ASSET_MISSING


def test_worker_when_component_scene_omits_background_calls_adapter(tmp_path: Path) -> None:
    job = CaptureJob.model_validate_json(
        JOB_JSON.replace('      "background_image": "backgrounds/exam.jpg",\n', ""),
    )
    adapter = NonceRecordingAdapter()

    result = CaptureWorker(adapter=adapter).run(
        job=job,
        input_root=tmp_path / "inputs",
        output_root=tmp_path / "outputs",
    )

    assert result.status is JobStatus.COMPLETED
    assert adapter.backgrounds == [None]


def test_worker_when_background_is_symlinked_rejects_resolved_source(tmp_path: Path) -> None:
    # Given a declared background path that is a symlink to a regular image
    input_root = tmp_path / "inputs"
    real_background = tmp_path / "outside" / "exam.jpg"
    real_background.parent.mkdir(parents=True)
    _ = real_background.write_bytes(b"source-image")
    declared_background = input_root / "backgrounds" / "exam.jpg"
    declared_background.parent.mkdir(parents=True)
    declared_background.symlink_to(real_background)
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker resolves the scene source
    result = CaptureWorker(adapter=SuccessfulAdapter()).run(
        job=job,
        input_root=input_root,
        output_root=tmp_path / "outputs",
    )

    # Then it fails closed instead of reading through the symlink
    failure = result.captures[0]
    assert failure.status == "failed"
    assert failure.error.code is ErrorCode.INPUT_ASSET_MISSING


def test_worker_when_output_root_is_file_returns_typed_runtime_error(tmp_path: Path) -> None:
    # Given a valid input and an output path occupied by a regular file
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    output_root = tmp_path / "outputs"
    _ = output_root.write_bytes(b"not-a-directory")
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker prepares its output root
    with pytest.raises(CaptureAdapterError) as raised:
        _ = CaptureWorker(adapter=SuccessfulAdapter()).run(
            job=job,
            input_root=input_root,
            output_root=output_root,
        )

    # Then the filesystem failure remains machine-readable
    assert raised.value.code is ErrorCode.SCENE_CAPTURE_FAILED


def test_worker_when_adapter_preserves_failure_evidence(tmp_path: Path) -> None:
    # Given the adapter captured a diagnostic frame before identifying a platform failure
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    output_root = tmp_path / "outputs"
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker records the typed adapter failure
    result = CaptureWorker(adapter=DiagnosticFailureAdapter()).run(
        job=job,
        input_root=input_root,
        output_root=output_root,
    )

    # Then the failed result points reviewers to the diagnostic PNG
    failure = result.captures[0]
    assert failure.status == "failed"
    assert failure.evidence_path == "job-01/lockscreen-01.png"


def test_worker_when_cleanup_also_fails(tmp_path: Path) -> None:
    # Given a primary export failure with a secondary cleanup failure
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker serializes the typed adapter failure
    result = CaptureWorker(adapter=PrimaryAndCleanupFailureAdapter()).run(
        job=job,
        input_root=input_root,
        output_root=tmp_path / "outputs",
    )

    # Then the primary code remains authoritative and cleanup evidence is retained
    failure = result.captures[0]
    assert failure.status == "failed"
    assert failure.error.code is ErrorCode.EXPORT_INVALID
    assert failure.error.cleanup_error == "Appium session cleanup failed"


def test_worker_when_adapter_error_is_oversized_then_failure_is_bounded(tmp_path: Path) -> None:
    # Given a valid input and an adapter that reports oversized diagnostic text
    input_root = tmp_path / "inputs"
    background = input_root / "backgrounds" / "exam.jpg"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"source-image")
    job = CaptureJob.model_validate_json(JOB_JSON)

    # When the worker converts the adapter error into its result contract
    result = CaptureWorker(adapter=OversizedFailureAdapter()).run(
        job=job,
        input_root=input_root,
        output_root=tmp_path / "outputs",
    )

    # Then the typed failure remains serializable at the 500-character boundary
    failure = result.captures[0]
    assert failure.status == "failed"
    assert len(failure.error.message) == 500
    assert failure.error.cleanup_error is not None
    assert len(failure.error.cleanup_error) == 500
