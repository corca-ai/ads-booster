from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from trace_capture.contracts.models import MarketingContext

if TYPE_CHECKING:
    from datetime import datetime

    from trace_capture.contracts.generation import MarketingContextBundle

_JAPANESE_STUDENT_ITEMS: Final = ("統計学 2限", "レポート提出", "ゼミ準備")
_DEFAULT_ITEMS: Final = ("重点タスク", "予定の確認", "今日の振り返り")


@dataclass(frozen=True, slots=True)
class SceneRecipe:
    scene_id: str
    locale: str
    context: MarketingContext
    reference_date: datetime
    trace_items: tuple[str, str, str]
    background_prompt: str


class ScenePlanner:
    def plan(self, bundle: MarketingContextBundle) -> SceneRecipe:
        items = self._trace_items(bundle)
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
            trace_items=items,
            background_prompt=self._background_prompt(bundle),
        )

    def _trace_items(self, bundle: MarketingContextBundle) -> tuple[str, str, str]:
        if bundle.promotion_material.trace_items is not None:
            return bundle.promotion_material.trace_items
        if (
            bundle.persona.locale == "ja-JP"
            and bundle.persona.occupation == "university_student"
            and bundle.promotion_material.concept == "exam_week"
        ):
            return _JAPANESE_STUDENT_ITEMS
        return _DEFAULT_ITEMS

    def _background_prompt(self, bundle: MarketingContextBundle) -> str:
        persona = bundle.persona
        material = bundle.promotion_material
        traits = ", ".join(persona.traits)
        interests = ", ".join(persona.interests)
        tones = ", ".join(material.tone)
        return (
            f"Vertical 9:16 lifestyle background for a {persona.age_group} {persona.occupation} "
            f"in {persona.country}. Personality: {traits}. Interests: {interests}. "
            f"Promote {material.feature} with the concept {material.concept}; mood: {tones}. "
            f"Create distinct campaign variation {bundle.variation_index}. "
            "Leave the upper and center areas calm for native lock-screen UI overlays. "
            "Do not include text, numbers, letters, logos, phones, calendars, icons, or UI."
        )
