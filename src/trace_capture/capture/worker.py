from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    SystemCaptureClock,
    path_has_symlink_component,
)
from trace_capture.contracts import (
    CaptureError,
    CaptureJob,
    CaptureProvenance,
    CaptureResult,
    CaptureScene,
    CompletedSceneCapture,
    DeviceTarget,
    ErrorCode,
    FailedSceneCapture,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    job_id: str
    device: DeviceTarget
    scene: CaptureScene
    background: Path | None
    destination: Path
    control: CaptureControl
    iphone_ui_destination: Path | None = None
    capture_nonce: str = field(default_factory=lambda: secrets.token_hex(32))


class SceneCaptureAdapter(Protocol):
    def capture(self, request: CaptureRequest) -> CaptureProvenance: ...


@dataclass(frozen=True, slots=True)
class CaptureExecutionOptions:
    timeout_seconds: float = 120.0
    cancel_file: Path | None = None


@dataclass(frozen=True, slots=True)
class CaptureWorker:
    adapter: SceneCaptureAdapter
    options: CaptureExecutionOptions = field(default_factory=CaptureExecutionOptions)

    def run(self, job: CaptureJob, input_root: Path, output_root: Path) -> CaptureResult:
        resolved_input_root = _prepare_input_root(input_root)
        if path_has_symlink_component(output_root):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="capture output root contains a symlink",
            )
        try:
            resolved_output_root = output_root.resolve()
            job_output_root = resolved_output_root / job.job_id
            job_output_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"capture output root could not be prepared: {error}",
            ) from error
        captures: list[CompletedSceneCapture | FailedSceneCapture] = []
        control = CaptureControl.start(
            timeout_seconds=self.options.timeout_seconds,
            cancel_file=self.options.cancel_file,
            clock=SystemCaptureClock(),
        )

        for scene in job.scenes:
            background = (
                resolved_input_root / scene.background_image
                if scene.background_image is not None
                else None
            )
            if background is not None and (
                path_has_symlink_component(background)
                or not background.is_relative_to(resolved_input_root)
                or not background.is_file()
            ):
                captures.append(
                    FailedSceneCapture(
                        scene_id=scene.scene_id,
                        status="failed",
                        error=CaptureError(
                            code=ErrorCode.INPUT_ASSET_MISSING,
                            message=f"background image is unavailable: {scene.background_image}",
                        ),
                    ),
                )
                continue

            destination = job_output_root / f"{scene.scene_id}.png"
            if path_has_symlink_component(destination):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="capture destination path contains a symlink",
                )
            request = CaptureRequest(
                job_id=job.job_id,
                device=job.device,
                scene=scene,
                background=background,
                destination=destination,
                control=control,
                iphone_ui_destination=resolved_input_root / "inputs" / "iphone-ui.png",
                capture_nonce=secrets.token_hex(32),
            )
            try:
                provenance = self.adapter.capture(request)
            except CaptureAdapterError as error:
                evidence_path = (
                    destination.relative_to(resolved_output_root).as_posix()
                    if not path_has_symlink_component(destination) and destination.is_file()
                    else None
                )
                captures.append(
                    FailedSceneCapture(
                        scene_id=scene.scene_id,
                        status="failed",
                        error=CaptureError(
                            code=error.code,
                            message=error.message[:500],
                            cleanup_error=(
                                error.cleanup_error[:500]
                                if error.cleanup_error is not None
                                else None
                            ),
                        ),
                        evidence_path=evidence_path,
                    ),
                )
                continue
            except OSError as error:
                captures.append(
                    FailedSceneCapture(
                        scene_id=scene.scene_id,
                        status="failed",
                        error=CaptureError(
                            code=ErrorCode.SCENE_CAPTURE_FAILED,
                            message=f"capture artifact could not be written: {error}"[:500],
                        ),
                    ),
                )
                continue

            captures.append(
                CompletedSceneCapture(
                    scene_id=scene.scene_id,
                    status="completed",
                    image_path=destination.relative_to(resolved_output_root).as_posix(),
                    provenance=provenance,
                ),
            )

        result = CaptureResult.from_captures(
            job_id=job.job_id,
            captures=tuple(captures),
        )
        result_path = job_output_root / "capture-result.json"
        if path_has_symlink_component(result_path):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="capture result path contains a symlink",
            )
        try:
            _ = result_path.write_text(
                result.model_dump_json(),
                encoding="utf-8",
            )
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"capture result could not be written: {error}",
            ) from error
        return result


def _prepare_input_root(input_root: Path) -> Path:
    try:
        return input_root if path_has_symlink_component(input_root) else input_root.resolve()
    except OSError as error:
        raise CaptureAdapterError(
            code=ErrorCode.INPUT_ASSET_MISSING,
            message=f"capture input root could not be prepared: {error}",
        ) from error
