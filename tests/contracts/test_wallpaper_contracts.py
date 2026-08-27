from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ads_booster.contracts import (
    WallpaperCellColor,
    WallpaperCellHeight,
    WallpaperComponent,
    WallpaperEvent,
    WallpaperExportManifest,
    WallpaperFontSize,
    WallpaperHeaderColor,
    WallpaperLayout,
    WallpaperPlan,
    WallpaperRow,
    WallpaperStyle,
    WallpaperTextColor,
)


def valid_event(*, title: str = "Study session") -> WallpaperEvent:
    return WallpaperEvent(
        title=title,
        starts_at=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
        ends_at=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
        is_all_day=False,
        color="#38BDF8",
    )


def valid_style() -> WallpaperStyle:
    return WallpaperStyle(
        text_color=WallpaperTextColor.WHITE,
        header_color=WallpaperHeaderColor.AUTO,
        cell_color=WallpaperCellColor.BLACK,
        font_size=WallpaperFontSize.NORMAL,
        cell_opacity=36,
        cell_blur=False,
        cell_height=WallpaperCellHeight.NORMAL,
        allow_two_line_title=True,
        image_scale=1,
        image_brightness=100,
        image_blur=0,
        image_dimming=0,
    )


def valid_row() -> WallpaperRow:
    return WallpaperRow(
        layout=WallpaperLayout.ONE_BY_ONE,
        components=(WallpaperComponent(title="Today", events=(valid_event(),)),),
    )


def test_parse_wallpaper_plan_when_every_runtime_choice_is_explicit() -> None:
    # Given a complete, user-directed wallpaper configuration
    # When the plan crosses the automation boundary
    plan = WallpaperPlan(
        schema_version="trace.wallpaper-plan.v1",
        request_id="jp-study-20260826",
        time_zone="Asia/Seoul",
        background_query="Tokyo study desk at dusk",
        reference_ids=("jp-reference-01",),
        style=valid_style(),
        rows=(
            WallpaperRow(
                layout=WallpaperLayout.ONE_BY_ONE,
                components=(
                    WallpaperComponent(
                        title="Today",
                        events=(valid_event(), valid_event(title="Dinner")),
                    ),
                ),
            ),
        ),
    )

    # Then no renderer or Appium setting needs an implied default
    assert plan.rows[0].components[0].events[1].title == "Dinner"
    assert plan.style.image_scale == 1
    assert plan.time_zone == "Asia/Seoul"


def test_parse_wallpaper_plan_when_reference_ids_are_explicitly_empty() -> None:
    # Given a plan with no supplied reference images
    # When the caller explicitly supplies an empty reference collection
    plan = WallpaperPlan(
        schema_version="trace.wallpaper-plan.v1",
        request_id="jp-study-20260826",
        time_zone="Asia/Seoul",
        background_query="Tokyo study desk at dusk",
        reference_ids=(),
        style=valid_style(),
        rows=(valid_row(),),
    )

    # Then optional references do not require a fabricated identifier
    assert plan.reference_ids == ()


def test_reject_wallpaper_plan_when_a_required_style_or_reference_is_missing() -> None:
    # Given an otherwise complete plan that leaves runtime choices implicit
    raw_plan = {
        "schema_version": "trace.wallpaper-plan.v1",
        "request_id": "jp-study-20260826",
        "time_zone": "Asia/Seoul",
        "background_query": "Tokyo study desk at dusk",
        "style": valid_style().model_dump(),
        "rows": (valid_row().model_dump(),),
    }

    # When it crosses the trust boundary
    # Then omitted reference IDs and rows cannot fall back to product defaults
    with pytest.raises(ValidationError):
        _ = WallpaperPlan.model_validate(raw_plan)

    raw_style = valid_style().model_dump(mode="json")
    del raw_style["image_dimming"]
    with pytest.raises(ValidationError):
        _ = WallpaperStyle.model_validate(raw_style)


def test_reject_wallpaper_plan_when_time_zone_is_not_iana() -> None:
    # Given a wallpaper plan using a display time zone unknown to IANA
    # When it crosses the trust boundary
    # Then rendering cannot fall back to the host time zone
    with pytest.raises(ValidationError):
        _ = WallpaperPlan(
            schema_version="trace.wallpaper-plan.v1",
            request_id="jp-study-20260826",
            time_zone="Mars/Olympus_Mons",
            background_query="Tokyo study desk at dusk",
            reference_ids=(),
            style=valid_style(),
            rows=(valid_row(),),
        )


def test_reject_wallpaper_style_when_cell_color_is_outside_trace_palette() -> None:
    # Given a style whose cell color cannot be selected in Trace
    raw_style = valid_style().model_dump(mode="json")
    raw_style["cell_color"] = "#101010"

    # When it crosses the automation contract
    # Then Appium cannot receive an unreachable palette value
    with pytest.raises(ValidationError):
        _ = WallpaperStyle.model_validate(raw_style)


def test_parse_all_day_event_when_its_time_range_is_intentionally_omitted() -> None:
    # Given an all-day event with no guessed start or end time
    # When it is parsed at the automation boundary
    event = WallpaperEvent(
        title="Exam day",
        starts_at=None,
        ends_at=None,
        is_all_day=True,
        color="#38BDF8",
    )

    # Then the explicit all-day flag is its complete time contract
    assert event.starts_at is None


@pytest.mark.parametrize(
    "event",
    [
        {
            "title": "Naive time",
            "starts_at": "2026-08-26T09:00:00",
            "ends_at": "2026-08-26T10:00:00",
            "is_all_day": False,
            "color": "#38BDF8",
        },
        {
            "title": "All day with only one bound",
            "starts_at": "2026-08-26T00:00:00Z",
            "ends_at": None,
            "is_all_day": True,
            "color": "#38BDF8",
        },
    ],
)
def test_reject_wallpaper_event_when_time_contract_is_ambiguous(event: dict[str, object]) -> None:
    # Given an event without a complete UTC range or a valid all-day omission
    # When it is parsed
    # Then its time semantics cannot be guessed from title text
    with pytest.raises(ValidationError):
        _ = WallpaperEvent.model_validate(event)


def test_reject_wallpaper_row_when_layout_and_component_count_differ() -> None:
    # Given a two-cell row claiming one wallpaper component
    raw_row = {
        "layout": "two_by_one",
        "components": ({"title": "Today", "events": (valid_event().model_dump(),)},),
    }

    # When the plan boundary parses it
    # Then Appium cannot silently invent a second component for the selected layout
    with pytest.raises(ValidationError):
        _ = WallpaperPlan.model_validate(
            {
                "schema_version": "trace.wallpaper-plan.v1",
                "request_id": "jp-study-20260826",
                "time_zone": "Asia/Seoul",
                "background_query": "Tokyo study desk at dusk",
                "reference_ids": ("jp-reference-01",),
                "style": valid_style().model_dump(),
                "rows": (raw_row,),
            }
        )


def test_parse_wallpaper_manifest_when_native_export_binding_is_complete() -> None:
    # Given a native full-wallpaper export record
    # When its provenance is parsed
    manifest = WallpaperExportManifest(
        schema_version="trace.wallpaper-export-manifest.v1",
        request_sha256="a" * 64,
        export_nonce="b" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        role="trace_wallpaper",
        artifact_sha256="c" * 64,
        width=1290,
        height=2796,
    )

    # Then the artifact is bound to a specific request, export, app, and device
    assert manifest.role == "trace_wallpaper"
