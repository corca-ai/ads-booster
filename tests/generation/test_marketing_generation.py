from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.connectors.trace.v1.scene_plan import recipe_for_wallpaper_plan
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
from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.runtime.generate_one import (
    GenerateOneOptions,
    GenerateOneRunner,
)
from ads_booster.search.image.background import SearchedBackground

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.worker import CaptureRequest
    from ads_booster.planning.scene_planner import SceneRecipe


@dataclass(frozen=True, slots=True)
class FixtureBackgroundFetcher:
    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 6), (12, 24, 48)).save(destination, format="PNG")
        return SearchedBackground(
            path=destination,
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            query=query,
            provider="fixture-search",
            image_url="https://images.example/background.png",
            source_url="https://example.com/background",
        )


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingBackgroundFetcher:
    queries: list[str] = field(default_factory=list)

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        self.queries.append(query)
        return FixtureBackgroundFetcher().fetch(query, destination)


@dataclass(frozen=True, slots=True)
class FixtureCaptureAdapter:
    def capture(self, request: CaptureRequest, plan: WallpaperPlan) -> CaptureProvenance:
        assert plan.request_id == "jp-student-exam" or plan.request_id.endswith("-ui")
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (4, 6), (12, 24, 48))
        image.save(request.destination, format="PNG")
        content = request.destination.read_bytes()
        return CaptureProvenance(
            request_sha256="a" * 64,
            artifact_sha256=hashlib.sha256(content).hexdigest(),
            bundle_id="com.corca.Trace",
            device_udid=request.device.udid,
            session_id="fixture-session",
            byte_size=len(content),
            width=4,
            height=6,
            source_modified_at_ns=1,
            source="native_appium",
            artifact_role="trace_wallpaper",
            native_export_nonce="b" * 64,
            native_export_binding_verified=True,
        )


def context() -> MarketingContextBundle:
    return MarketingContextBundle.model_validate(
        {
            "schema_version": "trace.marketing-context.v1",
            "request_id": "jp-student-exam",
            "persona": {
                "persona_id": "jp-university-student",
                "country": "JP",
                "locale": "ja-JP",
                "age_group": "20s",
                "occupation": "university_student",
                "traits": ["diligent", "cute"],
                "interests": ["study", "cafe"],
            },
            "promotion_material": {
                "promotion_material_id": "lock-screen-schedule",
                "feature": "lock_screen_schedule",
                "concept": "exam_week",
                "tone": ["warm", "focused"],
            },
            "reference_date": "2026-08-25T00:00:00Z",
            "device": {
                "kind": "simulator",
                "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
                "platform_version": "26.5",
                "device_name": "iPhone 17 Pro",
            },
        }
    )


def dynamic_recipe(bundle: MarketingContextBundle) -> SceneRecipe:
    references = tuple(item.reference_id for item in bundle.reference_images)
    plan = WallpaperPlan(
        schema_version="trace.wallpaper-plan.v1",
        request_id=bundle.request_id,
        time_zone="Asia/Tokyo",
        background_query="quiet student room natural light",
        reference_ids=references,
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
                layout=WallpaperLayout.ONE_BY_ONE,
                components=(
                    WallpaperComponent(
                        title="Focus block",
                        events=(
                            WallpaperEvent(
                                title="Research",
                                starts_at=datetime(2026, 8, 25, 0, tzinfo=UTC),
                                ends_at=datetime(2026, 8, 25, 1, tzinfo=UTC),
                                is_all_day=False,
                                color="#38BDF8",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    return recipe_for_wallpaper_plan(plan, bundle)


def test_generate_one_runner_when_context_is_valid_then_it_completes_one_image(
    tmp_path: Path,
) -> None:
    options = GenerateOneOptions(
        output_root=tmp_path / "generated",
        appium_server="http://127.0.0.1:4723",
        timeout_seconds=30,
    )

    result = GenerateOneRunner(
        options=options,
        background_fetcher=FixtureBackgroundFetcher(),
        capture_adapter=FixtureCaptureAdapter(),
    ).run_plan(context(), dynamic_recipe(context()))

    assert result.state.value == "completed"
    assert result.schema_version == "trace.run-result.v2"
    assert result.output_image == "outputs/final.png"
    assert (tmp_path / "generated" / "jp-student-exam" / "outputs" / "final.png").is_file()
    assert not (tmp_path / "generated" / "jp-student-exam" / "inputs" / "iphone-ui.png").exists()


def test_generate_one_runner_when_capture_is_ready_then_it_searches_one_background(
    tmp_path: Path,
) -> None:
    # Given a valid context and a capture adapter that produces the native Trace layer
    background_fetcher = RecordingBackgroundFetcher()

    # When the complete generation runner executes the bundle
    _ = GenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
        ),
        background_fetcher=background_fetcher,
        capture_adapter=FixtureCaptureAdapter(),
    ).run_plan(context(), dynamic_recipe(context()))

    # Then the background is searched once and the packaged system UI is not rendered
    assert len(background_fetcher.queries) == 1
    assert background_fetcher.queries[0].startswith("quiet student room natural light")
    assert background_fetcher.queries[0].endswith("no text no logo no phone no UI")
    assert not (tmp_path / "generated" / "jp-student-exam" / "inputs" / "iphone-ui.png").exists()


def test_generate_one_runner_does_not_stage_packaged_system_ui(
    tmp_path: Path,
) -> None:
    # Given two otherwise identical runs with different requested time and locale
    first = context().model_copy(
        update={
            "request_id": "first-ui",
            "reference_date": datetime(2026, 8, 26, 8, 42, tzinfo=UTC),
        }
    )
    second = context().model_copy(
        update={
            "request_id": "second-ui",
            "reference_date": datetime(2026, 8, 26, 17, 20, tzinfo=UTC),
            "persona": context().persona.model_copy(update={"country": "KR", "locale": "ko-KR"}),
        }
    )
    runner = GenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
        ),
        background_fetcher=FixtureBackgroundFetcher(),
        capture_adapter=FixtureCaptureAdapter(),
    )

    # When each recipe is generated through the native wallpaper path
    _ = runner.run_plan(first, dynamic_recipe(first))
    _ = runner.run_plan(second, dynamic_recipe(second))

    # Then neither run receives the packaged iPhone UI composition layer
    assert not (tmp_path / "generated" / "first-ui" / "inputs" / "iphone-ui.png").exists()
    assert not (tmp_path / "generated" / "second-ui" / "inputs" / "iphone-ui.png").exists()


def test_generate_one_runner_when_bundle_has_reference_then_it_uses_search_not_image_edit(
    tmp_path: Path,
) -> None:
    # Given a bundle bound to a verified workspace reference image
    reference = tmp_path / "assets" / "reference.png"
    reference.parent.mkdir()
    Image.new("RGB", (4, 6), (80, 120, 160)).save(reference, format="PNG")
    payload = context().model_dump(mode="json")
    payload["reference_images"] = [
        {
            "reference_id": "reference-one",
            "relative_path": "assets/reference.png",
            "media_type": "image/png",
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        }
    ]
    bundle = MarketingContextBundle.model_validate(payload)
    background_fetcher = RecordingBackgroundFetcher()

    # When the complete generation runner executes the bundle
    _ = GenerateOneRunner(
        options=GenerateOneOptions(
            output_root=tmp_path / "generated",
            appium_server="http://127.0.0.1:4723",
            timeout_seconds=30,
        ),
        background_fetcher=background_fetcher,
        capture_adapter=FixtureCaptureAdapter(),
    ).run_plan(bundle, dynamic_recipe(bundle))

    # Then the search query is constructed from context rather than the image-edit reference
    assert len(background_fetcher.queries) == 1
