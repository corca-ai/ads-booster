from ads_booster.contracts import (
    CaptureError,
    CaptureProvenance,
    CaptureResult,
    CompletedSceneCapture,
    ErrorCode,
    FailedSceneCapture,
    JobStatus,
    TraceRunResult,
    TraceRunState,
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


def test_trace_run_result_v2_when_wallpaper_is_complete_requires_no_component_artifact() -> None:
    # Given a request-bound full wallpaper export
    provenance = PROVENANCE.model_copy(
        update={
            "artifact_role": "trace_wallpaper",
            "native_export_binding_verified": True,
        }
    )

    # When the v2 result crosses the contract boundary
    result = TraceRunResult(
        schema_version="trace.run-result.v2",
        run_id="wallpaper-run",
        idempotency_key="wallpaper-run-v2",
        input_digest="c" * 64,
        state=TraceRunState.COMPLETED,
        output_image="outputs/final.png",
        output_image_sha256="b" * 64,
        capture_provenance=provenance,
    )

    # Then only the complete wallpaper artifact is claimed
    assert result.component_artifact is None
    assert result.capture_provenance is not None
    assert result.capture_provenance.artifact_role == "trace_wallpaper"
