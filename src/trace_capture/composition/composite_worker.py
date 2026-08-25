from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import UnidentifiedImageError

from trace_capture.capture.capture_safety import path_has_symlink_component
from trace_capture.composition.image_composer import (
    CanvasSize,
    CompositionLayers,
    LayerCompositionError,
    compose_marketing_image,
    normalize_ai_ui_layer,
)
from trace_capture.contracts import (
    CaptureError,
    ErrorCode,
    JobStatus,
    MarketingCompositeJob,
    MarketingCompositeResult,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompositeWorker:
    def run(
        self,
        job: MarketingCompositeJob,
        job_root: Path,
    ) -> MarketingCompositeResult:
        if path_has_symlink_component(job_root):
            return MarketingCompositeResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                layers=job.layers,
                errors=(
                    CaptureError(
                        code=ErrorCode.COMPOSITION_FAILED,
                        message="composite job root contains a symlink",
                    ),
                ),
            )
        root = job_root.resolve()
        destination = root / job.output_image
        normalized_ui = destination.with_name(f"{job.job_id}-iphone-ui.png")
        if path_has_symlink_component(destination) or path_has_symlink_component(normalized_ui):
            result = MarketingCompositeResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                layers=job.layers,
                errors=(
                    CaptureError(
                        code=ErrorCode.COMPOSITION_FAILED,
                        message="composite output path contains a symlink",
                    ),
                ),
            )
            self._write_result(result=result, destination=destination)
            return result
        source_paths = (
            ("background", job.layers.background),
            ("trace_components", job.layers.trace_components),
            ("iphone_ui", job.layers.iphone_ui),
        )
        resolved_sources: dict[str, Path] = {}
        for layer_name, relative_path in source_paths:
            source_candidate = root / relative_path
            source = source_candidate.resolve()
            if path_has_symlink_component(source_candidate):
                result = MarketingCompositeResult(
                    job_id=job.job_id,
                    status=JobStatus.FAILED,
                    layers=job.layers,
                    errors=(
                        CaptureError(
                            code=ErrorCode.INPUT_ASSET_MISSING,
                            message=f"composite layer contains a symlink: {layer_name}",
                        ),
                    ),
                )
                self._write_result(result=result, destination=destination)
                return result
            if not source.is_relative_to(root) or not source.is_file():
                result = MarketingCompositeResult(
                    job_id=job.job_id,
                    status=JobStatus.FAILED,
                    layers=job.layers,
                    errors=(
                        CaptureError(
                            code=ErrorCode.INPUT_ASSET_MISSING,
                            message=f"composite layer is unavailable: {layer_name}",
                        ),
                    ),
                )
                self._write_result(result=result, destination=destination)
                return result
            resolved_sources[layer_name] = source

        canvas = CanvasSize(width=job.canvas.width, height=job.canvas.height)
        try:
            normalize_ai_ui_layer(
                source=resolved_sources["iphone_ui"],
                destination=normalized_ui,
                canvas=canvas,
            )
            compose_marketing_image(
                layers=CompositionLayers(
                    background=resolved_sources["background"],
                    trace_components=resolved_sources["trace_components"],
                    iphone_ui=normalized_ui,
                ),
                destination=destination,
                canvas=canvas,
            )
        except (LayerCompositionError, OSError, UnidentifiedImageError) as error:
            result = MarketingCompositeResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                layers=job.layers,
                errors=(
                    CaptureError(
                        code=ErrorCode.COMPOSITION_FAILED,
                        message=f"marketing image composition failed: {error}",
                    ),
                ),
            )
            self._write_result(result=result, destination=destination)
            return result

        result = MarketingCompositeResult(
            job_id=job.job_id,
            status=JobStatus.COMPLETED,
            layers=job.layers,
            output_image=job.output_image,
            normalized_iphone_ui=normalized_ui.relative_to(root).as_posix(),
        )
        self._write_result(result=result, destination=destination)
        return result

    def _write_result(
        self,
        result: MarketingCompositeResult,
        destination: Path,
    ) -> None:
        if path_has_symlink_component(destination):
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        result_path = destination.with_name("composite-result.json")
        if path_has_symlink_component(result_path):
            return
        _ = result_path.write_text(result.model_dump_json(), encoding="utf-8")
