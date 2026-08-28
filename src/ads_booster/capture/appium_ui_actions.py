from __future__ import annotations

# noqa: SIZE_OK — one cohesive Appium UI boundary; splitting would separate coupled driver actions
# pyright: reportUnknownMemberType=false
# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver as AppiumWebDriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

from ads_booster.capture.capture_safety import (
    AlertCommandWebDriver,
    AlertPresenceWebDriver,
    AppiumCall,
    CaptureAdapterError,
    CaptureControl,
    ElementFindingWebDriver,
    WebDriverClient,
    WebDriverElement,
    alert_button_labels,
    startup_alert_button,
)
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from selenium.webdriver.remote.webelement import WebElement


ALERT_BUTTON_BY_IDENTIFIER = {
    "calendarEditDeleteConfirm": "삭제",
    "lockScreenWallpaperResetConfirm": "초기화",
}


@dataclass(frozen=True, slots=True)
class SeleniumElementAdapter:
    native: WebElement

    @property
    def element_id(self) -> str:
        return self.native.id

    def clear(self) -> None:
        self.native.clear()

    def click(self) -> None:
        self.native.click()

    def set_value(self, value: str) -> None:
        self.native.send_keys(value)

    def get_attribute(self, name: str) -> str | None:
        return self.native.get_attribute(name)


@dataclass(frozen=True, slots=True)
class AppiumUI:
    driver: WebDriverClient
    call: AppiumCall

    def click(self, identifier: str, control: CaptureControl) -> None:
        alert_button = ALERT_BUTTON_BY_IDENTIFIER.get(identifier)
        if alert_button is not None and isinstance(self.driver, AlertCommandWebDriver):
            driver = cast("AlertCommandWebDriver", self.driver)
            _ = self.call(
                lambda: driver.execute_script(
                    "mobile: alert",
                    {"action": "accept", "buttonLabel": alert_button},
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI control is unavailable: {identifier}",
                control,
            )
            return
        element = self.find(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        self.click_element(element, identifier, control)

    def click_element(
        self,
        element: WebDriverElement,
        identifier: str,
        control: CaptureControl,
    ) -> None:
        if (
            identifier == "calendar_settingsButton"
            or identifier.startswith("connectionCalendarEdit.")
        ) and isinstance(self.driver, AlertCommandWebDriver):
            driver = cast("AlertCommandWebDriver", self.driver)
            offset = (18, 18) if identifier == "calendar_settingsButton" else (180, 26)
            _ = self.call(
                lambda: driver.execute_script(
                    "mobile: tap",
                    {"elementId": element.element_id, "x": offset[0], "y": offset[1]},
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI control is unavailable: {identifier}",
                control,
            )
            return
        _ = self.call(
            (
                lambda: (
                    cast("AlertCommandWebDriver", self.driver).execute_script(
                        "mobile: tap",
                        {"elementId": element.element_id, "x": 18, "y": 18},
                    )
                    if identifier.startswith("calendarCreateColor.")
                    and isinstance(self.driver, AppiumWebDriver)
                    else element.click()
                )
            ),
            ErrorCode.SCENE_CAPTURE_FAILED,
            f"Trace UI control is unavailable: {identifier}",
            control,
        )

    def replace_text(
        self,
        identifier: str,
        value: str,
        control: CaptureControl,
    ) -> None:
        element = self.find(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        _ = self.call(
            element.clear,
            ErrorCode.SCENE_CAPTURE_FAILED,
            f"Trace UI text could not be cleared: {identifier}",
            control,
        )
        _ = self.call(
            lambda: element.set_value(value),
            ErrorCode.SCENE_CAPTURE_FAILED,
            f"Trace UI text could not be entered: {identifier}",
            control,
        )

    def set_value(
        self,
        identifier: str,
        value: str,
        control: CaptureControl,
    ) -> None:
        element = self.find(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        _ = self.call(
            lambda: element.set_value(value),
            ErrorCode.SCENE_CAPTURE_FAILED,
            f"Trace UI value could not be entered: {identifier}",
            control,
        )

    def set_toggle(
        self,
        identifier: str,
        enabled: bool,
        control: CaptureControl,
    ) -> None:
        element = self.find(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        raw_value = self.call(
            lambda: element.get_attribute("value"),
            ErrorCode.SCENE_CAPTURE_FAILED,
            f"Trace UI toggle could not be read: {identifier}",
            control,
        )
        current = raw_value is not None and raw_value.lower() in {"1", "true"}
        if current != enabled:
            _ = self.call(
                element.click,
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI toggle could not be changed: {identifier}",
                control,
            )

    def exists(self, identifier: str, control: CaptureControl) -> bool:
        return bool(self.find_all_now(AppiumBy.ACCESSIBILITY_ID, identifier, control))

    def wait_until_absent(self, identifier: str, control: CaptureControl) -> None:
        if not isinstance(self.driver, AppiumWebDriver):
            return
        for _ in range(20):
            if not self.exists(identifier, control):
                return
            control.wait(0.5)
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message=f"Trace UI control remained after deletion: {identifier}",
        )

    def back(self, control: CaptureControl) -> None:
        _ = self.call(
            self.driver.back,
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Trace UI navigation could not return to the previous screen",
            control,
        )

    def dismiss_alerts(self, control: CaptureControl) -> None:
        if not isinstance(self.driver, AlertCommandWebDriver) or not isinstance(
            self.driver,
            AlertPresenceWebDriver,
        ):
            return
        control.wait(1)
        for _ in range(6):
            try:
                _ = self.driver.switch_to.alert.text
            except WebDriverException:
                return
            try:
                raw_buttons = self.driver.execute_script(
                    "mobile: alert",
                    {"action": "getButtons"},
                )
            except WebDriverException:
                return
            labels = alert_button_labels(raw_buttons)
            if not labels:
                return
            button = startup_alert_button(labels)
            if button is None:
                return
            try:
                _ = self.driver.execute_script(
                    "mobile: alert",
                    {"action": "accept", "buttonLabel": button},
                )
            except WebDriverException:
                return
            control.wait(0.5)

    @staticmethod
    def _visible_element(
        driver: AppiumWebDriver,
        by: str,
        value: str,
    ) -> WebElement | None:
        for element in AppiumUI._visible_elements(driver, by, value):
            return element
        return None

    @staticmethod
    def _present_element(
        driver: AppiumWebDriver,
        by: str,
        value: str,
    ) -> WebElement | None:
        elements = AppiumUI._present_elements(driver, by, value)
        return elements[0] if elements else None

    @staticmethod
    def _present_elements(
        driver: AppiumWebDriver,
        by: str,
        value: str,
    ) -> list[WebElement]:
        return cast("list[WebElement]", driver.find_elements(by, value))

    @staticmethod
    def _visible_elements(
        driver: AppiumWebDriver,
        by: str,
        value: str,
    ) -> list[WebElement]:
        elements = cast("list[WebElement]", driver.find_elements(by, value))
        return [element for element in elements if element.is_displayed()]

    def find(
        self,
        by: str,
        value: str,
        control: CaptureControl,
    ) -> WebDriverElement:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            element = self.call(
                lambda: WebDriverWait(driver, control.remaining_seconds()).until(
                    lambda _: (
                        self._present_element(driver, by, value)
                        if value == "calendar_settingsButton"
                        or value.startswith("connectionCalendarEdit.")
                        else self._visible_element(driver, by, value)
                    ),
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI control is unavailable: {value}",
                control,
            )
            if element is None:
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message=f"Trace UI control is unavailable: {value}",
                )
            return SeleniumElementAdapter(element)
        if isinstance(driver, ElementFindingWebDriver):
            return self.call(
                lambda: driver.find_element(by, value),
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI control is unavailable: {value}",
                control,
            )
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot locate Trace UI controls",
        )

    def find_all_now(
        self,
        by: str,
        value: str,
        control: CaptureControl,
    ) -> list[WebDriverElement]:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            return [
                SeleniumElementAdapter(element)
                for element in self.call(
                    lambda: (
                        self._present_elements(driver, by, value)
                        if value == "calendar_settingsButton"
                        or value.startswith("connectionCalendarEdit.")
                        else self._visible_elements(driver, by, value)
                    ),
                    ErrorCode.SCENE_CAPTURE_FAILED,
                    f"Trace UI controls could not be inspected: {value}",
                    control,
                )
            ]
        if isinstance(driver, ElementFindingWebDriver):
            return self.call(
                lambda: driver.find_elements(by, value),
                ErrorCode.SCENE_CAPTURE_FAILED,
                f"Trace UI controls could not be inspected: {value}",
                control,
            )
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot inspect Trace UI controls",
        )
