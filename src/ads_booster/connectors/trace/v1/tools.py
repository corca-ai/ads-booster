from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol

from pydantic import ValidationError

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.connectors.trace.v1.scene_plan import (
    TraceScenePlanError,
    recipe_for_wallpaper_plan,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.run import TraceRunState
from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.contracts.wallpaper import WallpaperPlan  # noqa: TC001
from ads_booster.runtime.generate_one import GenerateOneError
from ads_booster.search.image.background import BackgroundSearchError
from ads_booster.tools.models import ToolContext, ToolResult

if TYPE_CHECKING:
    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.results import TraceRunResult
    from ads_booster.planning.scene_planner import SceneRecipe
    from ads_booster.transport.json_types import JsonObject


class TraceGenerateImageArgs(ContractModel):
    plan: WallpaperPlan


class TracePlannedImageRunner(Protocol):
    def run_plan(
        self,
        bundle: MarketingContextBundle,
        recipe: SceneRecipe,
    ) -> TraceRunResult: ...


@dataclass(frozen=True, slots=True)
class TraceGenerateMarketingImageTool:
    name: ClassVar[str] = "trace_generate_marketing_image"
    bundle: MarketingContextBundle
    runner: TracePlannedImageRunner

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Generate one Trace marketing image from a complete model-authored scene plan. "
                "Choose all card titles, item groupings, supported layouts, references, and the "
                "creative background query."
            ),
            parameters=TraceGenerateImageArgs.model_json_schema(),
            strict=True,
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = TraceGenerateImageArgs.model_validate_json(json.dumps(arguments))
            recipe = recipe_for_wallpaper_plan(parsed.plan, self.bundle)
        except (ValidationError, TraceScenePlanError) as error:
            return ToolResult(
                ok=False,
                output=str(error),
                error_code="trace_scene_plan_invalid",
            )
        if not context.approval.request(self.name, self.bundle.request_id):
            return ToolResult(
                ok=False,
                output="Trace marketing image generation was denied",
                error_code="approval_denied",
            )
        try:
            result = self.runner.run_plan(self.bundle, recipe)
        except (BackgroundSearchError, CaptureAdapterError, GenerateOneError, OSError) as error:
            return ToolResult(
                ok=False,
                output=str(error),
                error_code="trace_generation_failed",
            )
        if result.state is not TraceRunState.COMPLETED:
            return ToolResult(
                ok=False,
                output=result.model_dump_json(),
                error_code="trace_generation_failed",
            )
        provenance = result.capture_provenance
        if (
            provenance is None
            or provenance.source != "native_appium"
            or provenance.artifact_role != "trace_wallpaper"
            or not provenance.native_export_binding_verified
        ):
            return ToolResult(
                ok=False,
                output="Trace native export provenance is unverified",
                error_code="trace_provenance_unverified",
            )
        return ToolResult(ok=True, output=result.model_dump_json())
