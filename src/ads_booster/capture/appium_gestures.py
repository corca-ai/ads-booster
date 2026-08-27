from __future__ import annotations

from dataclasses import dataclass

from appium.webdriver.webdriver import WebDriver as AppiumWebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement

from ads_booster.capture.capture_safety import (
    AppiumCall,
    CaptureAdapterError,
    CaptureControl,
    PhotoAssetW3CTapWebDriver,
    WebDriverClient,
    WebDriverElement,
)
from ads_booster.contracts import ErrorCode


@dataclass(frozen=True, slots=True)
class PhotoAssetTapper:
    driver: WebDriverClient
    call: AppiumCall

    def tap(self, element: WebDriverElement, control: CaptureControl) -> None:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            _ = self.call(
                lambda: (
                    ActionChains(driver)
                    .move_to_element(WebElement(driver, element.element_id))
                    .click()
                    .perform()
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "PhotosPicker asset could not be selected",
                control,
            )
            return
        if isinstance(driver, PhotoAssetW3CTapWebDriver):
            _ = self.call(
                lambda: driver.execute_photo_asset_w3c_tap(element.element_id),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "PhotosPicker asset could not be selected",
                control,
            )
            return
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot W3C-tap a PhotosPicker asset",
        )
