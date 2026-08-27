from __future__ import annotations

# pyright: reportUnknownMemberType=false
# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, assert_never

from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver as AppiumWebDriver
from selenium.webdriver.support import expected_conditions as conditions
from selenium.webdriver.support.ui import WebDriverWait

from ads_booster.capture.appium_gestures import PhotoAssetTapper
from ads_booster.capture.appium_sliders import (
    WallpaperSlider,
    normalized_background_slider_values,
    normalized_slider_value,
)
from ads_booster.capture.capture_safety import (
    AppiumCall,
    CaptureAdapterError,
    CaptureControl,
    ElementFindingWebDriver,
    WebDriverClient,
    WebDriverElement,
)
from ads_booster.contracts import ErrorCode, WallpaperLayout, WallpaperPlan

if TYPE_CHECKING:
    from selenium.webdriver.remote.webelement import WebElement

QUADRANT_COMPONENT_ID: Final = "lockScreenWallpaperComponentSelect.quadrants"
FIRST_RECENT_PHOTO_PREDICATE: Final = "name == 'PXGGridLayout-Info'"


def wallpaper_layout_control_value(layout: WallpaperLayout) -> str:
    match layout:
        case WallpaperLayout.ONE_BY_ONE:
            return "1X1"
        case WallpaperLayout.TWO_BY_ONE:
            return "2X1"
        case WallpaperLayout.TWO_TOP_ONE_BOTTOM:
            return "2T1B"
        case WallpaperLayout.TWO_BY_TWO:
            return "2X2"
        case unreachable:
            assert_never(unreachable)


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
class WallpaperEditorDriver:
    driver: WebDriverClient
    call: AppiumCall

    def configure(
        self,
        plan: WallpaperPlan,
        select_background: bool,
        control: CaptureControl,
    ) -> None:
        components = self._find_elements(QUADRANT_COMPONENT_ID, control)
        layouts = tuple(row.layout for row in plan.rows)
        expected_count = len(layouts)
        if len(components) != expected_count:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=(
                    "Trace wallpaper component count does not match the requested plan: "
                    f"expected {expected_count}, found {len(components)}"
                ),
            )
        if plan.style.cell_blur and not select_background:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace wallpaper cell blur requires a selected background",
            )
        if select_background:
            self._configure_background(plan, control)
        for component, layout in zip(components, layouts, strict=True):
            self._configure_component(component, layout, plan, control)
        self._click_identifier("lockScreenWallpaperSave", control)

    def save(self, control: CaptureControl) -> None:
        self._click_identifier("lockScreenWallpaperSave", control)

    def _configure_component(
        self,
        component: WebDriverElement,
        layout: WallpaperLayout,
        plan: WallpaperPlan,
        control: CaptureControl,
    ) -> None:
        self._click(component, control)
        self._click_identifier("lockScreenWallpaperComponentLayout", control)
        self._click_identifier(
            f"lockScreenWallpaperComponentLayout.{wallpaper_layout_control_value(layout)}",
            control,
        )
        self._click_identifier(
            f"lockScreenWallpaperComponentTextColor.{plan.style.text_color.value}",
            control,
        )
        self._click_identifier("lockScreenWallpaperComponentHeaderColor", control)
        self._click_identifier(
            f"lockScreenWallpaperComponentHeaderColor.{plan.style.header_color.value}",
            control,
        )
        self._click_identifier(self._cell_color_identifier(plan.style.cell_color.value), control)
        self._click_identifier(
            f"lockScreenWallpaperComponentFontSize.{plan.style.font_size.value}",
            control,
        )
        self._set_value(
            "lockScreenWallpaperComponentCellOpacity",
            normalized_slider_value(WallpaperSlider.CELL_OPACITY, plan.style.cell_opacity),
            control,
        )
        self._click_identifier(
            f"lockScreenWallpaperComponentCellHeight.{plan.style.cell_height.value}",
            control,
        )
        self._set_toggle(
            "lockScreenWallpaperComponentTwoLineTitle",
            plan.style.allow_two_line_title,
            control,
        )
        self._click_identifier("lockScreenWallpaperComponentDetailClose", control)

    def _configure_background(self, plan: WallpaperPlan, control: CaptureControl) -> None:
        self._click_identifier("lockScreenWallpaperBackgroundPicker", control)
        photos = self._find_elements_by(
            AppiumBy.IOS_PREDICATE,
            FIRST_RECENT_PHOTO_PREDICATE,
            control,
        )
        if not photos:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="PhotosPicker does not expose a recent photo asset",
            )
        PhotoAssetTapper(self.driver, self.call).tap(photos[0], control)
        self._click_identifier("lockScreenWallpaperOptions", control)
        for identifier, value in normalized_background_slider_values(plan.style):
            self._set_value(identifier, value, control)
        self._set_toggle("lockScreenWallpaperComponentCellBlur", plan.style.cell_blur, control)
        self._click_identifier("lockScreenWallpaperOptionsClose", control)

    @staticmethod
    def _cell_color_identifier(color: str) -> str:
        suffix = {"#000000": "black", "#FFFFFF": "white"}.get(color, color)
        return f"lockScreenWallpaperComponentCellColor.{suffix}"

    def _click_identifier(self, identifier: str, control: CaptureControl) -> None:
        self._click(self._find_element_by(AppiumBy.ACCESSIBILITY_ID, identifier, control), control)

    def _click(self, element: WebDriverElement, control: CaptureControl) -> None:
        _ = self.call(
            element.click,
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Trace wallpaper control could not be selected",
            control,
        )

    def _set_value(self, identifier: str, value: str, control: CaptureControl) -> None:
        element = self._find_element_by(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        _ = self.call(
            lambda: element.set_value(value),
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Trace wallpaper control value could not be set",
            control,
        )

    def _set_toggle(
        self,
        identifier: str,
        enabled: bool,
        control: CaptureControl,
    ) -> None:
        element = self._find_element_by(AppiumBy.ACCESSIBILITY_ID, identifier, control)
        raw_value = self.call(
            lambda: element.get_attribute("value"),
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Trace wallpaper toggle state could not be read",
            control,
        )
        current = raw_value is not None and raw_value.lower() in {"1", "true"}
        if current != enabled:
            self._click(element, control)

    def _find_element_by(
        self,
        by: str,
        value: str,
        control: CaptureControl,
    ) -> WebDriverElement:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            element = self.call(
                lambda: WebDriverWait(driver, control.remaining_seconds()).until(
                    conditions.presence_of_element_located((by, value)),
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace wallpaper control is unavailable",
                control,
            )
            if element is False:
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Trace wallpaper control is unavailable",
                )
            return SeleniumElementAdapter(element)
        if isinstance(driver, ElementFindingWebDriver):
            return self.call(
                lambda: driver.find_element(by, value),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace wallpaper control is unavailable",
                control,
            )
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot configure Trace wallpaper",
        )

    def _find_elements(
        self,
        identifier: str,
        control: CaptureControl,
    ) -> list[WebDriverElement]:
        return self._find_elements_by(AppiumBy.ACCESSIBILITY_ID, identifier, control)

    def _find_elements_by(
        self,
        by: str,
        value: str,
        control: CaptureControl,
    ) -> list[WebDriverElement]:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            elements = self.call(
                lambda: WebDriverWait(driver, control.remaining_seconds()).until(
                    conditions.presence_of_all_elements_located(
                        (by, value),
                    ),
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace wallpaper components are unavailable",
                control,
            )
            return [SeleniumElementAdapter(element) for element in elements]
        if isinstance(driver, ElementFindingWebDriver):
            return self.call(
                lambda: driver.find_elements(by, value),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace wallpaper components are unavailable",
                control,
            )
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot configure Trace wallpaper components",
        )
