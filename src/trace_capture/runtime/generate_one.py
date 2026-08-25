from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, override

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
from trace_capture.planning.scene_planner import ScenePlanner
from trace_capture.runtime.trace_run import TraceRunRunner
from trace_capture.runtime.trace_run_capture import CaptureWorkerPort, LocalComposePort
from trace_capture.runtime.trace_run_store import JsonlTraceRunStore

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.capture.readiness import CaptureReadiness
    from trace_capture.capture.worker import SceneCaptureAdapter
    from trace_capture.contracts.generation import MarketingContextBundle
    from trace_capture.contracts.results import TraceRunResult
    from trace_capture.planning.scene_planner import SceneRecipe
    from trace_capture.search.image.background import SearchedBackground

BACKGROUND_MISSING: Final = "background_missing"
SYSTEM_UI_MISSING: Final = "system_ui_missing"
SYSTEM_UI_COPY_FAILED: Final = "system_ui_copy_failed"


@dataclass(frozen=True, slots=True)
class GenerateOneOptions:
    output_root: Path
    state_root: Path
    capture_output_root: Path
    iphone_ui_path: Path
    appium_server: str
    timeout_seconds: float
    capture_readiness: CaptureReadiness | None = None


@dataclass(frozen=True, slots=True)
class GenerateOneError(RuntimeError):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class BackgroundFetcher(Protocol):
    def fetch(self, query: str, destination: Path) -> SearchedBackground: ...


@dataclass(frozen=True, slots=True)
class GenerateOneRunner:
    options: GenerateOneOptions
    background_fetcher: BackgroundFetcher
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
        background = self.background_fetcher.fetch(recipe.background_query, background_path)
        background.write_provenance(job_root / "inputs" / "background-source.json")
        self._prepare_system_ui(system_ui_path)
        request = _build_request(bundle, recipe, job_root)
        runner = TraceRunRunner(
            store=JsonlTraceRunStore(root=self.options.state_root),
            capture_port=CaptureWorkerPort(
                adapter=self.capture_adapter,
                options=CaptureExecutionOptions(
                    timeout_seconds=self.options.timeout_seconds,
                    capture_iphone_ui=False,
                ),
                output_root=self.options.capture_output_root,
            ),
            compose_port=LocalComposePort(),
        )
        return runner.run(request=request, job_root=job_root)

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
        raise GenerateOneError(BACKGROUND_MISSING, "searched background artifact is unavailable")
    return TraceRunRequest(
        schema_version="trace.run-job.v1",
        run_id=bundle.request_id,
        idempotency_key=f"{bundle.request_id}-v1",
        capture_job=capture_job,
        composite_job=composite_job,
    )
