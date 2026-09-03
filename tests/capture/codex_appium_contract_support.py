from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.capture.codex_appium_job import (
    CodexAppiumJobContract,
    CodexAppiumJobIdentity,
)
from ads_booster.contracts import (
    DeviceKind,
    DeviceTarget,
    MarketingContextBundle,
    PersonaProfile,
    PreparedBackground,
    PromotionMaterial,
    TraceBackgroundSearchProvenance,
    TraceScheduleItem,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class V2JobInputs:
    task_id: str = "task-1"
    concept: str = "Planless native capture"
    device_name: str = "iPhone 17 Pro"
    country: str = "KR"
    locale: str = "ko-KR"
    time_zone: str = "Asia/Seoul"
    background_sha256: str = "a" * 64
    export_nonce: str = "b" * 64
    calendar_namespace: str = "trace-request-1"
    todo_calendar_namespace: str | None = None
    trace_items: tuple[str | JsonObject, ...] = ("Focus block",)
    trace_todos: tuple[str, ...] = ()
    request_id: str = "request-1"
    reference_date: datetime = datetime(2026, 8, 28, tzinfo=UTC)


_DEFAULT_V2_JOB_INPUTS = V2JobInputs()


def v2_contract(
    inputs: V2JobInputs = _DEFAULT_V2_JOB_INPUTS,
) -> CodexAppiumJobContract:
    device = DeviceTarget(
        kind=DeviceKind.SIMULATOR,
        udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        platform_version="26.0",
        device_name=inputs.device_name,
    )
    context = MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=inputs.request_id,
        campaign_id="campaign-1",
        persona=PersonaProfile(
            persona_id="persona-1",
            country=inputs.country,
            locale=inputs.locale,
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id="promotion-1",
            concept=inputs.concept,
            background_intent="quiet Seoul desk at dawn",
            trace_items=tuple(
                TraceScheduleItem.model_validate(item) for item in inputs.trace_items
            ),
            trace_todos=inputs.trace_todos,
        ),
        reference_date=inputs.reference_date,
        device=device,
    )
    return CodexAppiumJobContract(
        schema_version="trace.codex-appium-job.v2",
        identity=CodexAppiumJobIdentity(
            task_id=inputs.task_id,
            run_id="run-1",
            request_id=inputs.request_id,
            idempotency_key="hosted:task-1:request-1",
            candidate_id="candidate-1",
            candidate_revision=3,
        ),
        context=context,
        prepared_background=PreparedBackground(
            path="inputs/background.png",
            sha256=inputs.background_sha256,
            provenance=TraceBackgroundSearchProvenance(
                schema_version="trace.background-search.v1",
                artifact_path="inputs/background.png",
                artifact_sha256=inputs.background_sha256,
                query="quiet Seoul desk at dawn",
                provider="google-images",
                image_url="https://images.pexels.com/photo/1",
                source_url="https://www.pexels.com/photo/1",
            ),
        ),
        device=device,
        locale=inputs.locale,
        time_zone=inputs.time_zone,
        python_executable="/usr/bin/python3",
        appium_server="http://127.0.0.1:4723",
        bundle_id="com.corca.Trace",
        app_group_id="group.ai.corca.trace",
        calendar_namespace=inputs.calendar_namespace,
        todo_calendar_namespace=(
            inputs.todo_calendar_namespace or f"{inputs.calendar_namespace}-todos"
        ),
        export_nonce=inputs.export_nonce,
    )


__all__ = ["V2JobInputs", "v2_contract"]
