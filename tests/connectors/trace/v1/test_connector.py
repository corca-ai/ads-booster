from __future__ import annotations

# noqa: SIZE_OK -- image, reference, and review cases share connector fixtures
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.agent.runs import (
    AgentGoal,
    AgentObservation,
    AgentRun,
    AgentRunId,
    CompletionDisposition,
    ConnectorId,
    ObservationKind,
    ToolPolicy,
)
from ads_booster.connectors.trace.v1 import (
    TraceMarketingConnector,
)
from ads_booster.contracts import (
    CaptureProvenance,
    WallpaperCellColor,
    WallpaperCellHeight,
    WallpaperComponent,
    WallpaperEvent,
    WallpaperFontSize,
    WallpaperHeaderColor,
    WallpaperLayout,
    WallpaperPlan,
    WallpaperRow,
    WallpaperStyle,
    WallpaperTextColor,
)
from ads_booster.contracts.generation import (
    GenerationReferenceImage,
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunState
from ads_booster.tools.models import ToolContext

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ads_booster.planning.scene_planner import SceneRecipe
    from ads_booster.transport.json_types import JsonObject


def bundle() -> MarketingContextBundle:
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id="dynamic-scene",
        variation_index=3,
        persona=PersonaProfile(
            persona_id="jp-student",
            country="JP",
            locale="ja-JP",
            age_group="20s",
            occupation="university student",
            traits=("curious", "busy"),
            interests=("study", "music"),
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id="semester-launch",
            feature="lock screen schedule",
            concept="balanced exam week",
            tone=("observational",),
            trace_items=("08:30 経済学", "12:00 友達と昼食", "16:00 図書館", "20:00 ライブ"),
        ),
        reference_date=datetime(2026, 8, 26, tzinfo=UTC),
        device=DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
    )


def plan() -> WallpaperPlan:
    event_specs = (
        ("経済学", datetime(2026, 8, 25, 23, 30, tzinfo=UTC)),
        ("図書館", datetime(2026, 8, 26, 7, tzinfo=UTC)),
        ("友達と昼食", datetime(2026, 8, 26, 3, tzinfo=UTC)),
        ("ライブ", datetime(2026, 8, 26, 11, tzinfo=UTC)),
    )
    events = tuple(
        WallpaperEvent(
            title=title,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            is_all_day=False,
            color="#38BDF8",
        )
        for title, starts_at in event_specs
    )
    return WallpaperPlan(
        schema_version="trace.wallpaper-plan.v1",
        request_id="dynamic-scene",
        time_zone="Asia/Tokyo",
        background_query="Tokyo student room evening concert poster colors vertical wallpaper",
        reference_ids=(),
        style=WallpaperStyle(
            text_color=WallpaperTextColor.BLACK,
            header_color=WallpaperHeaderColor.WHITE,
            cell_color=WallpaperCellColor.PURPLE,
            font_size=WallpaperFontSize.LARGE,
            cell_opacity=47,
            cell_blur=True,
            cell_height=WallpaperCellHeight.TALL,
            allow_two_line_title=True,
            image_scale=1.4,
            image_brightness=135,
            image_blur=17,
            image_dimming=42,
        ),
        rows=(
            WallpaperRow(
                layout=WallpaperLayout.TWO_BY_ONE,
                components=(
                    WallpaperComponent(title="授業と集中", events=events[:2]),
                    WallpaperComponent(title="人と音楽", events=events[2:]),
                ),
            ),
        ),
    )


def completed_result() -> TraceRunResult:
    digest = "a" * 64
    return TraceRunResult(
        schema_version="trace.run-result.v2",
        run_id="dynamic-scene",
        idempotency_key="dynamic-scene-v2",
        input_digest="b" * 64,
        state=TraceRunState.COMPLETED,
        output_image="outputs/final.png",
        output_image_sha256=digest,
        capture_provenance=CaptureProvenance(
            request_sha256="c" * 64,
            artifact_sha256=digest,
            bundle_id="com.corca.Trace",
            device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            session_id="appium-session",
            byte_size=1024,
            width=1206,
            height=2622,
            source_modified_at_ns=1,
            source="native_appium",
            artifact_role="trace_wallpaper",
            native_export_nonce="d" * 64,
            native_export_binding_verified=True,
        ),
    )


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingRunner:
    result: TraceRunResult
    calls: list[tuple[MarketingContextBundle, SceneRecipe]] = field(default_factory=list)

    def run_plan(self, bundle: MarketingContextBundle, recipe: SceneRecipe) -> TraceRunResult:
        self.calls.append((bundle, recipe))
        return self.result


@dataclass(frozen=True, slots=True)
class AllowApproval:
    def request(self, action: str, detail: str) -> bool:
        del action, detail
        return True


def agent_run(history: tuple[JsonObject, ...] = ()) -> AgentRun:
    return AgentRun(
        run_id=AgentRunId("core-run-1"),
        connector_id=ConnectorId("trace-marketing"),
        connector_version="1.0.0",
        goal=AgentGoal(
            objective="Create a dynamic Trace marketing image",
            success_criteria=("native artifact awaits review",),
            context={},
        ),
        tool_policy=ToolPolicy(allow=("trace_generate_marketing_image",)),
        history=history,
    )


def test_connector_executes_a_model_authored_scene_without_rewriting_creative_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a model-authored plan with non-template headers and semantic groups
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    runner = RecordingRunner(completed_result())
    connector = TraceMarketingConnector(bundle(), runner)
    tool = connector.tools(agent_run().goal)[0]

    # When the semantic Trace capability executes the plan
    result = tool.execute(
        {"plan": plan().model_dump(mode="json")},
        ToolContext(tmp_path, AllowApproval(), ()),
    )

    # Then the exact creative choices reach the native runner with only safety suffixing
    assert result.ok
    recipe = runner.calls[0][1]
    assert tuple(component.title for component in recipe.trace_data.rows[0].components) == (
        "授業と集中",
        "人と音楽",
    )
    assert recipe.trace_data.rows[0].components[0].items == ("経済学", "図書館")
    assert recipe.background_query.startswith("Tokyo student room evening")
    assert recipe.background_query.endswith("no text no logo no phone no UI")
    assert recipe.wallpaper_plan == plan()
    assert recipe.wallpaper_plan.time_zone == "Asia/Tokyo"


def local_time_case(
    starts_at: datetime,
    title: str,
) -> tuple[MarketingContextBundle, WallpaperPlan]:
    contextual = bundle().model_copy(
        update={
            "promotion_material": bundle().promotion_material.model_copy(
                update={"trace_items": ("09:30 제품 설계 리뷰",)}
            )
        }
    )
    event = WallpaperEvent(
        title=title,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        is_all_day=False,
        color="#38BDF8",
    )
    candidate = plan().model_copy(
        update={
            "time_zone": "Asia/Seoul",
            "rows": (
                WallpaperRow(
                    layout=WallpaperLayout.ONE_BY_ONE,
                    components=(WallpaperComponent(title="업무", events=(event,)),),
                ),
            ),
        }
    )
    return contextual, candidate


def test_connector_reconstructs_source_label_in_explicit_plan_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given 00:30 UTC, a clean display title, Asia/Seoul, and an unrelated host timezone
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    contextual, candidate = local_time_case(
        datetime(2026, 8, 26, 0, 30, tzinfo=UTC),
        "제품 설계 리뷰",
    )
    runner = RecordingRunner(completed_result())

    # When the connector validates the promotion-owned 09:30 source label
    result = (
        TraceMarketingConnector(contextual, runner)
        .tools(agent_run().goal)[0]
        .execute(
            {"plan": candidate.model_dump(mode="json")},
            ToolContext(tmp_path, AllowApproval(), ()),
        )
    )

    # Then local time matches while the native display title remains clean
    assert result.ok
    native_event = runner.calls[0][1].wallpaper_plan.rows[0].components[0].events[0]
    assert native_event.title == "제품 설계 리뷰"
    assert native_event.starts_at == datetime(2026, 8, 26, 0, 30, tzinfo=UTC)


def test_connector_rejects_utc_time_mistaken_for_plan_local_time(tmp_path: Path) -> None:
    # Given 09:30 UTC would render as 18:30 in Asia/Seoul
    contextual, candidate = local_time_case(
        datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
        "제품 설계 리뷰",
    )
    runner = RecordingRunner(completed_result())

    # When the connector reconstructs the source label in the plan timezone
    result = (
        TraceMarketingConnector(contextual, runner)
        .tools(agent_run().goal)[0]
        .execute(
            {"plan": candidate.model_dump(mode="json")},
            ToolContext(tmp_path, AllowApproval(), ()),
        )
    )

    # Then the typed local-time mismatch blocks native side effects
    assert not result.ok
    assert "trace_local_time_mismatch" in result.output
    assert runner.calls == []


def test_connector_rejects_time_prefix_duplicated_in_display_title(tmp_path: Path) -> None:
    # Given a correctly converted event whose display title repeats the source time prefix
    contextual, candidate = local_time_case(
        datetime(2026, 8, 26, 0, 30, tzinfo=UTC),
        "09:30 제품 설계 리뷰",
    )
    runner = RecordingRunner(completed_result())

    # When canonical source reconstruction adds the local prefix
    result = (
        TraceMarketingConnector(contextual, runner)
        .tools(agent_run().goal)[0]
        .execute(
            {"plan": candidate.model_dump(mode="json")},
            ToolContext(tmp_path, AllowApproval(), ()),
        )
    )

    # Then duplicated time text is rejected before native rendering
    assert not result.ok
    assert "trace_local_time_mismatch" in result.output
    assert runner.calls == []


def test_connector_injects_verified_reference_pixels_as_model_context(tmp_path: Path) -> None:
    # Given a campaign reference whose bytes and digest are rooted in the workspace
    reference = tmp_path / "assets" / "reference.png"
    reference.parent.mkdir(parents=True)
    Image.new("RGB", (4, 6), (20, 40, 60)).save(reference, format="PNG")
    digest = sha256(reference.read_bytes()).hexdigest()
    contextual = bundle().model_copy(
        update={
            "reference_images": (
                GenerationReferenceImage(
                    reference_id="reference-a",
                    relative_path="assets/reference.png",
                    media_type="image/png",
                    sha256=digest,
                ),
            )
        }
    )
    connector = TraceMarketingConnector(
        contextual,
        RecordingRunner(completed_result()),
        reference_root=tmp_path,
    )

    # When the Agent requests connector-owned context
    messages = connector.context_messages(agent_run().goal)

    # Then the provider receives labeled image pixels without changing canonical history
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {
        "type": "input_text",
        "text": '{"reference_id":"reference-a","sha256":"' + digest + '"}',
    }
    image = content[1]
    assert isinstance(image, dict)
    assert image["type"] == "input_image"
    assert str(image["image_url"]).startswith("data:image/png;base64,")


def test_connector_ignores_an_artifact_invalidated_by_human_review() -> None:
    # Given a verified artifact is followed by a durable rejection boundary
    output: JsonObject = {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": completed_result().model_dump_json(),
    }
    rejected = agent_run((output,)).model_copy(
        update={
            "observations": (
                AgentObservation(
                    sequence=1,
                    kind=ObservationKind.APPROVAL,
                    summary="구성이 너무 일반적입니다",
                    data={"accepted": False, "history_length": 1, "note": "재구성"},
                ),
            )
        }
    )
    connector = TraceMarketingConnector(bundle(), RecordingRunner(completed_result()))

    # When completion is checked before and after a replacement tool result
    invalidated = connector.completed_result(rejected)
    replaced = connector.completed_result(
        rejected.model_copy(update={"history": (*rejected.history, output)})
    )

    # Then only the result produced after the rejection can satisfy completion
    assert invalidated is None
    assert replaced is not None


def test_connector_rejects_a_plan_that_drops_an_explicit_trace_item(tmp_path: Path) -> None:
    # Given a model plan that silently omits one promotion-owned item
    runner = RecordingRunner(completed_result())
    connector = TraceMarketingConnector(bundle(), runner)
    invalid = plan().model_copy(
        update={
            "rows": (
                WallpaperRow(
                    layout=WallpaperLayout.ONE_BY_ONE,
                    components=(
                        WallpaperComponent(
                            title="授業だけ",
                            events=plan().rows[0].components[0].events,
                        ),
                    ),
                ),
            )
        }
    )

    # When the connector validates the domain input boundary
    result = connector.tools(agent_run().goal)[0].execute(
        {"plan": invalid.model_dump(mode="json")},
        ToolContext(tmp_path, AllowApproval(), ()),
    )

    # Then native side effects never start and the model receives a typed correction
    assert not result.ok
    assert result.error_code == "trace_scene_plan_invalid"
    assert runner.calls == []


def test_connector_rejects_a_wallpaper_plan_for_another_request(tmp_path: Path) -> None:
    # Given a complete wallpaper plan bound to a different request
    runner = RecordingRunner(completed_result())
    connector = TraceMarketingConnector(bundle(), runner)
    invalid = plan().model_copy(update={"request_id": "different-request"})

    # When the connector validates the request binding
    result = connector.tools(agent_run().goal)[0].execute(
        {"plan": invalid.model_dump(mode="json")},
        ToolContext(tmp_path, AllowApproval(), ()),
    )

    # Then native side effects never start
    assert not result.ok
    assert result.error_code == "trace_scene_plan_invalid"
    assert runner.calls == []


def test_connector_rejects_a_plan_that_ignores_a_supplied_reference(tmp_path: Path) -> None:
    # Given the connector context contains a verified reference but the plan omits it
    reference = tmp_path / "reference.png"
    Image.new("RGB", (4, 6), (20, 40, 60)).save(reference, format="PNG")
    contextual = bundle().model_copy(
        update={
            "reference_images": (
                GenerationReferenceImage(
                    reference_id="reference-a",
                    relative_path="reference.png",
                    media_type="image/png",
                    sha256=sha256(reference.read_bytes()).hexdigest(),
                ),
            )
        }
    )
    runner = RecordingRunner(completed_result())

    # When the connector validates the model-authored reference selection
    result = (
        TraceMarketingConnector(contextual, runner)
        .tools(agent_run().goal)[0]
        .execute(
            {"plan": plan().model_dump(mode="json")},
            ToolContext(tmp_path, AllowApproval(), ()),
        )
    )

    # Then the missing reference blocks native side effects
    assert not result.ok
    assert result.error_code == "trace_scene_plan_invalid"
    assert runner.calls == []


def test_connector_requires_native_provenance_before_human_review(tmp_path: Path) -> None:
    # Given the semantic tool produced a request-bound native artifact
    runner = RecordingRunner(completed_result())
    connector = TraceMarketingConnector(bundle(), runner)
    tool_result = connector.tools(agent_run().goal)[0].execute(
        {"plan": plan().model_dump(mode="json")},
        ToolContext(tmp_path, AllowApproval(), ()),
    )
    run = agent_run(
        (
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": tool_result.output,
            },
        )
    )

    # When the model proposes completion
    decision = connector.validate_completion(run, "ready")

    # Then connector evidence suspends the Core run at the human review boundary
    assert decision.disposition is CompletionDisposition.AWAITING_APPROVAL
    assert decision.data["output_image"] == "outputs/final.png"
    assert decision.data["output_image_sha256"] == "a" * 64
