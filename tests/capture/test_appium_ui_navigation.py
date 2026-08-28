from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from ads_booster.capture.appium_session import appium_call
from ads_booster.capture.appium_ui_actions import AppiumUI
from ads_booster.capture.appium_ui_data import WallpaperDataDriver
from ads_booster.capture.capture_safety import CaptureControl

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonValue


@dataclass(frozen=True, slots=True)
class RecordingElement:
    calls: list[str]
    element_id: str = "settings-element"

    def clear(self) -> None:
        self.calls.append("clear")

    def click(self) -> None:
        self.calls.append("element_click")

    def set_value(self, value: str) -> None:
        self.calls.append(f"set_value:{value}")

    def get_attribute(self, name: str) -> str | None:
        self.calls.append(f"get_attribute:{name}")
        return None


@final
class HiddenSettingsDriver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.quick_create_open = True
        self.settings_open = False

    @property
    def session_id(self) -> str:
        return "hidden-settings-session"

    def lock(self, seconds: int) -> None:
        del seconds

    def is_locked(self) -> bool:
        return False

    def unlock(self) -> None:
        return None

    def save_screenshot(self, filename: str) -> bool:
        del filename
        return True

    def back(self) -> None:
        self.calls.append("back")

    def quit(self) -> None:
        return None

    def find_element(self, by: str, value: str) -> RecordingElement:
        self.calls.append(f"find:{by}:{value}")
        return RecordingElement(self.calls)

    def find_elements(self, by: str, value: str) -> list[RecordingElement]:
        self.calls.append(f"find_all:{by}:{value}")
        if value == "settingsConnectionButton" and self.settings_open:
            return [RecordingElement(self.calls)]
        if value == "calendar_settingsButton":
            return [RecordingElement(self.calls)]
        return []

    def execute_script(self, script: str, *args: JsonValue) -> None:
        del args
        self.calls.append(f"execute_script:{script}")
        if self.quick_create_open:
            self.quick_create_open = False
            return
        self.settings_open = True


def test_open_settings_when_wrapper_is_hidden_taps_until_settings_are_open() -> None:
    # Given Trace's quick-create dim hides a present calendar settings wrapper
    driver = HiddenSettingsDriver()
    data = WallpaperDataDriver(AppiumUI(driver, appium_call))

    # When automation opens settings
    data.open_wallpaper_editor(CaptureControl.start(5))

    # Then native taps dismiss the dim and open the settings screen
    assert driver.settings_open is True
    assert driver.calls.count("execute_script:mobile: tap") == 2
    assert driver.calls.count("element_click") == 2
