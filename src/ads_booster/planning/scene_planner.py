from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.models import MarketingContext, TraceData
    from ads_booster.contracts.wallpaper import WallpaperPlan


@dataclass(frozen=True, slots=True)
class SceneRecipe:
    scene_id: str
    locale: str
    context: MarketingContext
    reference_date: datetime
    trace_data: TraceData
    background_query: str
    wallpaper_plan: WallpaperPlan
