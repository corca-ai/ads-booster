from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.contracts import ErrorCode, WallpaperEvent, WallpaperPlan

if TYPE_CHECKING:
    from ads_booster.capture.appium_ui_actions import AppiumUI


@dataclass(frozen=True, slots=True)
class CalendarColor:
    name: str
    red: int
    green: int
    blue: int


CALENDAR_COLORS: Final = (
    CalendarColor("red", 255, 59, 48),
    CalendarColor("orange", 255, 149, 0),
    CalendarColor("yellow", 255, 204, 0),
    CalendarColor("green", 52, 199, 89),
    CalendarColor("mint", 0, 199, 190),
    CalendarColor("teal", 48, 176, 199),
    CalendarColor("cyan", 50, 173, 230),
    CalendarColor("blue", 0, 122, 255),
    CalendarColor("indigo", 88, 86, 214),
    CalendarColor("purple", 175, 82, 222),
    CalendarColor("pink", 255, 45, 85),
    CalendarColor("brown", 162, 132, 94),
)


@dataclass(frozen=True, slots=True)
class OwnedCalendar:
    title: str
    color_name: str
    component_index: int
    event: WallpaperEvent


def nearest_calendar_color(hex_color: str) -> str:
    red = int(hex_color[1:3], 16)
    green = int(hex_color[3:5], 16)
    blue = int(hex_color[5:7], 16)
    return min(
        CALENDAR_COLORS,
        key=lambda color: (
            (red - color.red) ** 2 + (green - color.green) ** 2 + (blue - color.blue) ** 2,
            color.name,
        ),
    ).name


def owned_calendars(plan: WallpaperPlan) -> tuple[OwnedCalendar, ...]:
    request_token = sha256(plan.request_id.encode()).hexdigest()[:12]
    calendars: list[OwnedCalendar] = []
    component_index = 0
    for row in plan.rows:
        for component in row.components:
            for event_index, event in enumerate(component.events):
                calendars.append(
                    OwnedCalendar(
                        title=(
                            f"TraceAuto-{request_token}-{component_index + 1}-{event_index + 1}"
                        ),
                        color_name=nearest_calendar_color(event.color),
                        component_index=component_index,
                        event=event,
                    ),
                )
            component_index += 1
    return tuple(calendars)


@dataclass(frozen=True, slots=True)
class WallpaperDataDriver:
    ui: AppiumUI

    def provision(
        self,
        plan: WallpaperPlan,
        reference_date: datetime,
        control: CaptureControl,
    ) -> tuple[OwnedCalendar, ...]:
        calendars = owned_calendars(plan)
        self._open_settings(control)
        self.ui.click("settingsConnectionButton", control)
        self.ui.click("connectionCalendarTab", control)
        if self.ui.exists("connectionCalendarPermissionButton", control):
            self.ui.click("connectionCalendarPermissionButton", control)
            self.ui.dismiss_alerts(control)
        for calendar in calendars:
            edit_identifier = f"connectionCalendarEdit.{calendar.title}"
            self.ui.replace_text("connectionCalendarSearch", calendar.title, control)
            if self.ui.exists(edit_identifier, control):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message=f"request-owned calendar already exists: {calendar.title}",
                )
        for calendar in calendars:
            self.ui.click("connectionAddCalendar", control)
            self.ui.replace_text("calendarCreateName", calendar.title, control)
            self.ui.click(f"calendarCreateColor.{calendar.color_name}", control)
            self.ui.click("calendarCreateSave", control)
        self.ui.click("connectionBackButton", control)
        self.ui.click("settingsCloseButton", control)
        for calendar in calendars:
            self._create_event(calendar, plan, reference_date, control)
        control.wait(1)
        return calendars

    def open_wallpaper_editor(self, control: CaptureControl) -> None:
        self._open_settings(control)
        self.ui.click("settingsWallpaperInfo", control)
        control.wait(0.5)
        self.ui.click("wallpaperInfoEditorButton", control)

    def _open_settings(self, control: CaptureControl) -> None:
        if self.ui.exists("settingsConnectionButton", control):
            return
        while not self.ui.exists("calendar_settingsButton", control):
            self.ui.back(control)
        self.ui.click("calendar_settingsButton", control)

    def cleanup(
        self,
        plan: WallpaperPlan,
        control: CaptureControl,
    ) -> None:
        calendars = owned_calendars(plan)
        self._open_settings(control)
        self.ui.click("settingsConnectionButton", control)
        self.ui.click("connectionCalendarTab", control)
        for calendar in calendars:
            edit_identifier = f"connectionCalendarEdit.{calendar.title}"
            self.ui.replace_text("connectionCalendarSearch", calendar.title, control)
            if not self.ui.exists(edit_identifier, control):
                continue
            self.ui.click(edit_identifier, control)
            self.ui.click("calendarEditDelete", control)
            self.ui.click("calendarEditDeleteConfirm", control)
            self.ui.wait_until_absent(edit_identifier, control)

    def _create_event(
        self,
        calendar: OwnedCalendar,
        plan: WallpaperPlan,
        reference_date: datetime,
        control: CaptureControl,
    ) -> None:
        event = calendar.event
        time_zone = ZoneInfo(plan.time_zone)
        if event.starts_at is None or event.ends_at is None:
            local_start = reference_date.astimezone(time_zone).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            local_end = local_start + timedelta(days=1)
        else:
            local_start = event.starts_at.astimezone(time_zone)
            local_end = event.ends_at.astimezone(time_zone)
        quick_create_text = (
            f"{local_start:%Y-%m-%d} {event.title}"
            if event.is_all_day
            else f"{local_start:%Y-%m-%d %H:%M} {event.title}"
        )
        self.ui.click("quickAddEntryButton", control)
        self.ui.replace_text("quickCreate_textField", quick_create_text, control)
        self.ui.click("quickCreate_expandButton", control)
        self.ui.replace_text("editView_titleField", event.title, control)
        self.ui.click("editView_eventType", control)
        self.ui.set_toggle("editView_allDayToggle", event.is_all_day, control)
        date_format = "%Y-%m-%dT00:00%z" if event.is_all_day else "%Y-%m-%dT%H:%M%z"
        self.ui.set_value(
            "editView_startDateInput",
            local_start.strftime(date_format),
            control,
        )
        self.ui.set_value(
            "editView_endDateInput",
            local_end.strftime(date_format),
            control,
        )
        self.ui.replace_text("editView_calendarInput", calendar.title, control)
        self.ui.click("editView_backButton", control)
        self.ui.click("quickCreate_createButton", control)
