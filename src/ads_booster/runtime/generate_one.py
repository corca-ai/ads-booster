from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol, override

from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.capture.worker import CaptureRequest
from ads_booster.contracts import CaptureScene, TraceRunResult, TraceRunState, WallpaperPlan

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.readiness import CaptureReadiness
    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.models import CaptureProvenance
    from ads_booster.planning.scene_planner import SceneRecipe
    from ads_booster.search.image.background import SearchedBackground

BACKGROUND_MISSING: Final = "background_missing"
WALLPAPER_ARTIFACT_INVALID: Final = "wallpaper_artifact_invalid"


@dataclass(frozen=True, slots=True)
class GenerateOneOptions:
    output_root: Path
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


class WallpaperCaptureAdapter(Protocol):
    def capture(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan,
    ) -> CaptureProvenance: ...


@dataclass(frozen=True, slots=True)
class GenerateOneRunner:
    options: GenerateOneOptions
    background_fetcher: BackgroundFetcher
    capture_adapter: WallpaperCaptureAdapter

    def run_plan(
        self,
        bundle: MarketingContextBundle,
        recipe: SceneRecipe,
    ) -> TraceRunResult:
        job_root = self.options.output_root / bundle.request_id
        background_path = job_root / "inputs" / "background.png"
        background = self.background_fetcher.fetch(recipe.background_query, background_path)
        background.write_provenance(job_root / "inputs" / "background-source.json")
        if not background_path.is_file():
            raise GenerateOneError(
                BACKGROUND_MISSING,
                "searched background artifact is unavailable",
            )
        output = job_root / "outputs" / "final.png"
        provenance = self.capture_adapter.capture(
            CaptureRequest(
                job_id=f"{bundle.request_id}-wallpaper",
                device=bundle.device,
                scene=CaptureScene(
                    scene_id=recipe.scene_id,
                    locale=recipe.locale,
                    capture_target="trace_wallpaper",
                    background_image="inputs/background.png",
                    reference_date=recipe.reference_date,
                    trace_data=recipe.trace_data,
                ),
                background=background_path,
                destination=output,
                control=CaptureControl.start(self.options.timeout_seconds),
            ),
            recipe.wallpaper_plan,
        )
        try:
            output_digest = sha256(output.read_bytes()).hexdigest()
        except OSError as error:
            raise GenerateOneError(
                WALLPAPER_ARTIFACT_INVALID,
                "native wallpaper export is unavailable",
            ) from error
        if (
            output_digest != provenance.artifact_sha256
            or provenance.artifact_role != "trace_wallpaper"
            or not provenance.native_export_binding_verified
        ):
            raise GenerateOneError(
                WALLPAPER_ARTIFACT_INVALID,
                "native wallpaper export does not match verified provenance",
            )
        input_digest = sha256(
            f"{bundle.model_dump_json()}\n{recipe.wallpaper_plan.model_dump_json()}".encode()
        ).hexdigest()
        return TraceRunResult(
            schema_version="trace.run-result.v2",
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v2",
            input_digest=input_digest,
            state=TraceRunState.COMPLETED,
            output_image="outputs/final.png",
            output_image_sha256=output_digest,
            capture_provenance=provenance,
        )
