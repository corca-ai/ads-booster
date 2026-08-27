from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from appium.webdriver.common.appiumby import AppiumBy

from ads_booster.capture.appium_wallpaper import wallpaper_layout_control_value
from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.contracts import ErrorCode, WallpaperPlan

if TYPE_CHECKING:
    from ads_booster.capture.appium_ui_actions import AppiumUI
    from ads_booster.capture.appium_ui_data import OwnedCalendar

QUADRANT_COMPONENT_ID: Final = "lockScreenWallpaperComponentSelect.quadrants"


@dataclass(frozen=True, slots=True)
class WallpaperStructureDriver:
    ui: AppiumUI

    def configure(
        self,
        plan: WallpaperPlan,
        calendars: tuple[OwnedCalendar, ...],
        control: CaptureControl,
    ) -> None:
        self.ui.click("lockScreenWallpaperReset", control)
        self.ui.click("lockScreenWallpaperResetConfirm", control)
        components = self.ui.find_all_now(
            AppiumBy.ACCESSIBILITY_ID,
            QUADRANT_COMPONENT_ID,
            control,
        )
        expected_count = len(plan.rows)
        if len(components) > expected_count:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=(
                    "Trace wallpaper reset produced more components than requested: "
                    f"expected {expected_count}, found {len(components)}"
                ),
            )
        for _ in range(expected_count - len(components)):
            self.ui.click("lockScreenWallpaperComponentAdd", control)
            self.ui.click("lockScreenWallpaperComponentKind.quadrants", control)
        components = self.ui.find_all_now(
            AppiumBy.ACCESSIBILITY_ID,
            QUADRANT_COMPONENT_ID,
            control,
        )
        if len(components) != expected_count:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=(
                    "Trace wallpaper component creation did not reach the requested count: "
                    f"expected {expected_count}, found {len(components)}"
                ),
            )
        flat_cell_index = 0
        component_index = 0
        for row_index, row in enumerate(plan.rows):
            self.ui.click_element(
                components[row_index],
                QUADRANT_COMPONENT_ID,
                control,
            )
            self.ui.click("lockScreenWallpaperComponentLayout", control)
            layout_value = wallpaper_layout_control_value(row.layout)
            layout_identifier = f"lockScreenWallpaperComponentLayout.{layout_value}"
            self.ui.click(
                layout_identifier,
                control,
            )
            for component in row.components:
                self.ui.click(
                    f"lockScreenWallpaperCalendarPicker.{flat_cell_index}",
                    control,
                )
                selected = tuple(
                    calendar
                    for calendar in calendars
                    if calendar.component_index == component_index
                )
                if not selected:
                    message = (
                        f"wallpaper component has no request-owned calendar: {component_index}"
                    )
                    raise CaptureAdapterError(
                        code=ErrorCode.SCENE_CAPTURE_FAILED,
                        message=message,
                    )
                for calendar in selected:
                    self.ui.replace_text(
                        "lockScreenWallpaperCalendarSearch",
                        calendar.title,
                        control,
                    )
                    self.ui.click(f"lockScreenWallpaperCalendar.{calendar.title}", control)
                self.ui.replace_text(
                    "lockScreenWallpaperDisplayName",
                    component.title,
                    control,
                )
                self.ui.click("lockScreenWallpaperCalendarSelectionBack", control)
                component_index += 1
                flat_cell_index += 1
            self.ui.click("lockScreenWallpaperComponentDetailClose", control)
