from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final, override
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import (
    ContractModel,
    Identifier,
    MarketingContext,
    TraceComponent,
    TraceComponentLayout,
    TraceComponentRow,
    TraceData,
)
from ads_booster.planning.scene_planner import SceneRecipe

if TYPE_CHECKING:
    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.wallpaper import WallpaperEvent, WallpaperPlan

BACKGROUND_SAFETY_SUFFIX: Final = "vertical wallpaper no text no logo no phone no UI"
REFERENCE_IDS_DUPLICATED: Final = "reference_ids_duplicated"
REFERENCE_IDS_DUPLICATED_MESSAGE: Final = "reference_ids_used must be unique"
TRACE_ITEMS_MISMATCH: Final = "trace_items_mismatch"
TRACE_ITEMS_MISMATCH_MESSAGE: Final = (
    "the plan must use every promotion-owned Trace item exactly once"
)
TRACE_LOCAL_TIME_MISMATCH: Final = "trace_local_time_mismatch"
TRACE_LOCAL_TIME_MISMATCH_MESSAGE: Final = (
    "promotion Trace items must match each event's plan-time-zone local HH:MM and clean title"
)
REFERENCE_NOT_USED: Final = "reference_not_used"
REFERENCE_NOT_USED_MESSAGE: Final = "the plan must use at least one supplied reference"
REFERENCE_UNKNOWN: Final = "reference_unknown"
REFERENCE_UNKNOWN_MESSAGE: Final = "the plan used a reference outside the supplied context"
REQUEST_ID_MISMATCH: Final = "request_id_mismatch"
REQUEST_ID_MISMATCH_MESSAGE: Final = "the wallpaper plan must belong to the context request"
LEGACY_PLAN_UNSUPPORTED: Final = "legacy_scene_plan_unsupported"
LEGACY_PLAN_UNSUPPORTED_MESSAGE: Final = (
    "TraceScenePlan cannot execute the full wallpaper path; use WallpaperPlan"
)


@dataclass(frozen=True, slots=True)
class TraceScenePlanError(RuntimeError):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class TraceScenePlan(ContractModel):
    trace_data: TraceData
    background_query: Annotated[str, Field(min_length=1, max_length=500)]
    reference_ids_used: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def require_unique_reference_ids(self) -> TraceScenePlan:
        if len(set(self.reference_ids_used)) != len(self.reference_ids_used):
            raise PydanticCustomError(
                REFERENCE_IDS_DUPLICATED,
                REFERENCE_IDS_DUPLICATED_MESSAGE,
            )
        return self

    def recipe_for(self, bundle: MarketingContextBundle) -> SceneRecipe:
        """Validate domain inputs and create the mechanical Trace recipe."""
        explicit_items = bundle.promotion_material.trace_items
        if explicit_items is not None and Counter(explicit_items) != Counter(self.trace_data.items):
            raise TraceScenePlanError(
                TRACE_ITEMS_MISMATCH,
                TRACE_ITEMS_MISMATCH_MESSAGE,
            )
        available_references = {item.reference_id for item in bundle.reference_images} | set(
            bundle.promotion_material.reference_ids
        )
        used_references = set(self.reference_ids_used)
        if available_references and not used_references:
            raise TraceScenePlanError(
                REFERENCE_NOT_USED,
                REFERENCE_NOT_USED_MESSAGE,
            )
        if not used_references <= available_references:
            raise TraceScenePlanError(
                REFERENCE_UNKNOWN,
                REFERENCE_UNKNOWN_MESSAGE,
            )
        context = MarketingContext(
            country=bundle.persona.country,
            persona_id=bundle.persona.persona_id,
            promotion_material_id=bundle.promotion_material.promotion_material_id,
        )
        return SceneRecipe(
            scene_id=f"{bundle.request_id}-scene",
            locale=bundle.persona.locale,
            context=context,
            reference_date=bundle.reference_date,
            trace_data=self.trace_data,
            background_query=f"{self.background_query} {BACKGROUND_SAFETY_SUFFIX}",
            wallpaper_plan=_legacy_plan_is_not_supported(),
        )


def recipe_for_wallpaper_plan(
    plan: WallpaperPlan,
    bundle: MarketingContextBundle,
) -> SceneRecipe:
    if plan.request_id != bundle.request_id:
        raise TraceScenePlanError(REQUEST_ID_MISMATCH, REQUEST_ID_MISMATCH_MESSAGE)
    plan_zone = ZoneInfo(plan.time_zone)
    canonical_source_items = tuple(
        _canonical_source_item(event, plan_zone)
        for row in plan.rows
        for component in row.components
        for event in component.events
    )
    explicit_items = bundle.promotion_material.trace_items
    if explicit_items is not None and Counter(explicit_items) != Counter(canonical_source_items):
        raise TraceScenePlanError(
            TRACE_LOCAL_TIME_MISMATCH,
            TRACE_LOCAL_TIME_MISMATCH_MESSAGE,
        )
    available_references = {item.reference_id for item in bundle.reference_images} | set(
        bundle.promotion_material.reference_ids
    )
    used_references = set(plan.reference_ids)
    if available_references and not used_references:
        raise TraceScenePlanError(REFERENCE_NOT_USED, REFERENCE_NOT_USED_MESSAGE)
    if not used_references <= available_references:
        raise TraceScenePlanError(REFERENCE_UNKNOWN, REFERENCE_UNKNOWN_MESSAGE)
    context = MarketingContext(
        country=bundle.persona.country,
        persona_id=bundle.persona.persona_id,
        promotion_material_id=bundle.promotion_material.promotion_material_id,
    )
    return SceneRecipe(
        scene_id=f"{bundle.request_id}-scene",
        locale=bundle.persona.locale,
        context=context,
        reference_date=bundle.reference_date,
        trace_data=_trace_data_from_wallpaper_plan(plan),
        background_query=f"{plan.background_query} {BACKGROUND_SAFETY_SUFFIX}",
        wallpaper_plan=plan,
    )


def _trace_data_from_wallpaper_plan(plan: WallpaperPlan) -> TraceData:
    return TraceData(
        rows=tuple(
            TraceComponentRow(
                layout=TraceComponentLayout(row.layout.value),
                components=tuple(
                    TraceComponent(
                        title=component.title,
                        items=tuple(event.title for event in component.events),
                    )
                    for component in row.components
                ),
            )
            for row in plan.rows
        )
    )


def _canonical_source_item(event: WallpaperEvent, time_zone: ZoneInfo) -> str:
    if event.starts_at is None:
        return event.title
    local_time = event.starts_at.astimezone(time_zone).strftime("%H:%M")
    return f"{local_time} {event.title}"


def _legacy_plan_is_not_supported() -> WallpaperPlan:
    raise TraceScenePlanError(
        LEGACY_PLAN_UNSUPPORTED,
        LEGACY_PLAN_UNSUPPORTED_MESSAGE,
    )
