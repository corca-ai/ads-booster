from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Final, override

from trace_capture.capture.capture_safety import CaptureControl
from trace_capture.capture.worker import CaptureExecutionOptions
from trace_capture.contracts import (
    CaptureJob,
    CaptureScene,
    ComponentExportCanvas,
    CompositeCanvas,
    CompositeLayers,
    MarketingCompositeJob,
    TraceData,
    TraceRunRequest,
)
from trace_capture.contracts.results import ComposeCompleted, ComposeOutcome, ToolFailed
from trace_capture.contracts.run import TraceRunErrorCode, TraceRunFailure
from trace_capture.planning.scene_planner import ScenePlanner
from trace_capture.providers.errors import ProviderError
from trace_capture.providers.image_generation import ImageGenerationRequest, ImageReferenceInput
from trace_capture.runtime.trace_run import TraceRunRunner
from trace_capture.runtime.trace_run_capture import CaptureWorkerPort
from trace_capture.runtime.trace_run_store import JsonlTraceRunStore

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.capture.readiness import CaptureReadiness
    from trace_capture.capture.worker import SceneCaptureAdapter
    from trace_capture.contracts.generation import MarketingContextBundle
    from trace_capture.contracts.results import TraceRunResult
    from trace_capture.planning.scene_planner import SceneRecipe
    from trace_capture.providers.image_generation import ImageGenerationPort

BACKGROUND_PATH_MISMATCH: Final = "background_path_mismatch"
SYSTEM_UI_MISSING: Final = "system_ui_missing"
SYSTEM_UI_COPY_FAILED: Final = "system_ui_copy_failed"
BACKGROUND_MISSING: Final = "background_missing"
REFERENCE_PATH_DENIED: Final = "reference_path_denied"
IMAGE_LAYER_MISSING: Final = "image_layer_missing"
FINAL_IMAGE_PROMPT: Final = (
    "Create the final Trace marketing image from exactly three supplied layers. "
    "Preserve the native Trace lock-screen component text and geometry, preserve the iPhone "
    "system UI structure, and blend the external background naturally behind them. "
    "Do not add new text, icons, dates, notifications, or unrelated UI. "
    "Return one polished vertical marketing image with the supplied composition intact."
)


@dataclass(frozen=True, slots=True)
class GenerateOneOptions:
    output_root: Path
    state_root: Path
    capture_output_root: Path
    iphone_ui_path: Path
    reference_root: Path
    appium_server: str
    timeout_seconds: float
    image_model: str
    capture_readiness: CaptureReadiness | None = None


@dataclass(frozen=True, slots=True)
class GenerateOneError(RuntimeError):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class ImageModelComposePort:
    image_generator: ImageGenerationPort
    model: str

    def compose(self, run_id: str, job: MarketingCompositeJob, job_root: Path) -> ComposeOutcome:
        _ = run_id
        destination = job_root / job.output_image
        try:
            references = tuple(
                _layer_reference(job_root, path)
                for path in (
                    job.layers.background,
                    job.layers.trace_components,
                    job.layers.iphone_ui,
                )
            )
            generated = self.image_generator.generate(
                ImageGenerationRequest(
                    prompt=FINAL_IMAGE_PROMPT,
                    destination=destination,
                    model=self.model,
                    reference_images=references,
                )
            )
        except (GenerateOneError, OSError, ProviderError) as error:
            return ToolFailed(
                failure=TraceRunFailure(
                    code=TraceRunErrorCode.COMPOSE_FAILED,
                    message=f"Image Model final composition failed: {error}",
                )
            )
        if generated.path != destination:
            return ToolFailed(
                failure=TraceRunFailure(
                    code=TraceRunErrorCode.COMPOSE_FAILED,
                    message="Image Model returned an unexpected final image path",
                )
            )
        return ComposeCompleted(output_image=destination)


@dataclass(frozen=True, slots=True)
class GenerateOneRunner:
    options: GenerateOneOptions
    image_generator: ImageGenerationPort
    capture_adapter: SceneCaptureAdapter
    planner: ScenePlanner = field(default_factory=ScenePlanner)

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        recipe = self.planner.plan(bundle)
        if self.options.capture_readiness is not None:
            self.options.capture_readiness.ensure(
                bundle.device,
                CaptureControl.start(timeout_seconds=self.options.timeout_seconds),
            )
        job_root = self.options.output_root / bundle.request_id
        background_path = job_root / "inputs" / "background.png"
        system_ui_path = job_root / "inputs" / "iphone-ui.png"
        self._prepare_system_ui(system_ui_path)
        generated = self.image_generator.generate(
            ImageGenerationRequest(
                prompt=recipe.background_prompt,
                destination=background_path,
                model=self.options.image_model,
                reference_images=self._reference_images(bundle),
            )
        )
        if generated.path != background_path:
            raise GenerateOneError(
                BACKGROUND_PATH_MISMATCH,
                "image generator returned an unexpected artifact path",
            )
        request = _build_request(bundle, recipe, job_root)
        runner = TraceRunRunner(
            store=JsonlTraceRunStore(root=self.options.state_root),
            capture_port=CaptureWorkerPort(
                adapter=self.capture_adapter,
                options=CaptureExecutionOptions(timeout_seconds=self.options.timeout_seconds),
                output_root=self.options.capture_output_root,
            ),
            compose_port=ImageModelComposePort(
                image_generator=self.image_generator,
                model=self.options.image_model,
            ),
        )
        return runner.run(request=request, job_root=job_root)

    def _reference_images(
        self,
        bundle: MarketingContextBundle,
    ) -> tuple[ImageReferenceInput, ...]:
        root = self.options.reference_root.resolve()
        references: list[ImageReferenceInput] = []
        for reference in bundle.reference_images:
            path = (root / reference.relative_path).resolve()
            if not path.is_relative_to(root):
                raise GenerateOneError(
                    REFERENCE_PATH_DENIED,
                    f"reference path must stay inside its configured root: {path}",
                )
            references.append(
                ImageReferenceInput(
                    path=path,
                    mime_type=reference.media_type,
                    sha256=reference.sha256,
                )
            )
        return tuple(references)

    def _prepare_system_ui(self, destination: Path) -> None:
        if not self.options.iphone_ui_path.is_file():
            raise GenerateOneError(
                SYSTEM_UI_MISSING,
                f"iPhone system UI asset is unavailable: {self.options.iphone_ui_path}",
            )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(self.options.iphone_ui_path, destination)
        except OSError as error:
            raise GenerateOneError(
                SYSTEM_UI_COPY_FAILED,
                f"iPhone system UI asset could not be staged: {destination}",
            ) from error


def _build_request(
    bundle: MarketingContextBundle,
    recipe: SceneRecipe,
    job_root: Path,
) -> TraceRunRequest:
    capture_job = CaptureJob(
        schema_version="trace.capture-job.v1",
        job_id=f"{bundle.request_id}-capture",
        context=recipe.context,
        device=bundle.device,
        scenes=(
            CaptureScene(
                scene_id=recipe.scene_id,
                locale=recipe.locale,
                capture_target="trace_components",
                background_image="inputs/background.png",
                component_canvas=ComponentExportCanvas(width=1206, height=2622),
                reference_date=recipe.reference_date,
                trace_data=TraceData(items=recipe.trace_items),
            ),
        ),
    )
    composite_job = MarketingCompositeJob(
        schema_version="trace.marketing-composite-job.v2",
        job_id=f"{bundle.request_id}-composite",
        context=recipe.context,
        canvas=CompositeCanvas(width=1290, height=2796),
        layers=CompositeLayers(
            background="inputs/background.png",
            trace_components="work/trace-components.png",
            iphone_ui="inputs/iphone-ui.png",
        ),
        output_image="outputs/final.png",
    )
    if not (job_root / "inputs" / "background.png").is_file():
        raise GenerateOneError(BACKGROUND_MISSING, "generated background artifact is unavailable")
    return TraceRunRequest(
        schema_version="trace.run-job.v1",
        run_id=bundle.request_id,
        idempotency_key=f"{bundle.request_id}-v1",
        capture_job=capture_job,
        composite_job=composite_job,
    )


def _layer_reference(root: Path, relative_path: str) -> ImageReferenceInput:
    candidate = root / relative_path
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise GenerateOneError(
            IMAGE_LAYER_MISSING,
            f"Image Model layer is unavailable: {relative_path}",
        )
    content = resolved.read_bytes()
    return ImageReferenceInput(
        path=resolved,
        mime_type="image/png",
        sha256=sha256(content).hexdigest(),
    )
