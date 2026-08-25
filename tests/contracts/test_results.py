from trace_capture.contracts import (
    CaptureError,
    CaptureProvenance,
    CaptureResult,
    CompletedSceneCapture,
    ErrorCode,
    FailedSceneCapture,
    JobStatus,
)

PROVENANCE = CaptureProvenance(
    request_sha256="a" * 64,
    artifact_sha256="b" * 64,
    bundle_id="com.corca.Trace",
    device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
    session_id="appium-session-01",
    byte_size=1024,
    width=1290,
    height=2796,
    source_modified_at_ns=1,
)


def test_build_result_when_all_scenes_complete() -> None:
    # Given every requested scene produced a PNG
    captures = (
        CompletedSceneCapture(
            scene_id="lockscreen-01",
            status="completed",
            image_path="outputs/job-01/lockscreen-01.png",
            provenance=PROVENANCE,
        ),
    )

    # When the worker builds its result contract
    result = CaptureResult.from_captures(job_id="job-01", captures=captures)

    # Then the job is completed without job-level errors
    assert result.status is JobStatus.COMPLETED
    assert result.errors == ()


def test_build_result_when_some_scenes_fail() -> None:
    # Given one scene completed and another failed
    captures = (
        CompletedSceneCapture(
            scene_id="lockscreen-01",
            status="completed",
            image_path="outputs/job-01/lockscreen-01.png",
            provenance=PROVENANCE,
        ),
        FailedSceneCapture(
            scene_id="lockscreen-02",
            status="failed",
            error=CaptureError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="lock screen capture command failed",
            ),
        ),
    )

    # When the worker builds its result contract
    result = CaptureResult.from_captures(job_id="job-01", captures=captures)

    # Then the job records an explicit partial outcome
    assert result.status is JobStatus.PARTIAL


def test_build_result_when_every_scene_fails() -> None:
    # Given no requested scene produced an artifact
    captures = (
        FailedSceneCapture(
            scene_id="lockscreen-01",
            status="failed",
            error=CaptureError(
                code=ErrorCode.APPIUM_SESSION_FAILED,
                message="Appium session could not start",
            ),
        ),
    )

    # When the worker builds its result contract
    result = CaptureResult.from_captures(job_id="job-01", captures=captures)

    # Then the job is failed
    assert result.status is JobStatus.FAILED
