from __future__ import annotations

# pyright: reportUnknownMemberType=false
# pyright: reportUnnecessaryComparison=false
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, assert_never, cast, runtime_checkable

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.client_config import AppiumClientConfig
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import HTTPError as Urllib3HttpError

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.appium_process import (
    ProcessArguments,
    build_component_process_arguments,
    build_configuration_process_arguments,
    capture_request_digest,
)
from ads_booster.capture.appium_ui_actions import AppiumUI
from ads_booster.capture.appium_ui_data import WallpaperDataDriver
from ads_booster.capture.appium_ui_wallpaper import WallpaperStructureDriver
from ads_booster.capture.appium_wallpaper import WallpaperEditorDriver
from ads_booster.capture.capture_safety import (
    AlertCommandWebDriver,
    AlertPresenceWebDriver,
    CaptureAdapterError,
    CaptureControl,
    WebDriverClient,
    alert_button_labels,
    path_has_symlink_component,
    startup_alert_button,
)
from ads_booster.contracts import ErrorCode, WallpaperPlan

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from ads_booster.capture.worker import CaptureRequest

TRACE_BUNDLE_ID = "com.corca.Trace"


@runtime_checkable
class RestartableWebDriver(Protocol):
    def terminate_app(self, bundle_id: str) -> bool: ...
    def activate_app(self, bundle_id: str) -> object: ...


def build_xcuitest_options(
    request: CaptureRequest,
    process_arguments: ProcessArguments | None = None,
) -> XCUITestOptions:
    options = XCUITestOptions()
    options.platform_name = "iOS"
    options.automation_name = "XCUITest"
    options.device_name = request.device.device_name
    options.platform_version = request.device.platform_version
    options.udid = request.device.udid
    options.bundle_id = TRACE_BUNDLE_ID
    options.no_reset = True
    options.force_app_launch = True
    options.use_new_wda = False
    options.wait_for_quiescence = False
    options.wait_for_idle_timeout = 1
    _ = options.set_capability(
        "appium:newCommandTimeout",
        max(60, math.ceil(request.control.remaining_seconds()) + 5),
    )
    wda_timeout_ms = math.ceil(request.control.remaining_seconds() * 1000)
    options.wda_launch_timeout = wda_timeout_ms
    options.wda_connection_timeout = wda_timeout_ms
    options.wda_startup_retries = 20
    options.wda_startup_retry_interval = 30000
    _ = options.set_capability(
        "appium:acceptAlertButtonSelector",
        "**/XCUIElementTypeButton[`label == '전체 접근 허용'`]",
    )
    _ = options.set_capability("appium:language", request.scene.locale.split("-")[0])
    _ = options.set_capability("appium:locale", request.scene.locale.replace("-", "_"))
    _ = options.set_capability(
        "appium:processArguments",
        process_arguments or build_component_process_arguments(request),
    )
    _ = options.set_capability("appium:sessionName", capture_request_digest(request))
    return options


class AppiumSession(Protocol):
    def reset_application(self, control: CaptureControl) -> None: ...

    def configure_wallpaper(
        self,
        plan: WallpaperPlan,
        select_background: bool,
        control: CaptureControl,
        reference_date: datetime,
    ) -> None: ...
    def cleanup_wallpaper(self, plan: WallpaperPlan, control: CaptureControl) -> None: ...
    def configure_components(self, control: CaptureControl) -> None: ...
    def session_id(self, control: CaptureControl) -> str: ...
    def lock(self, seconds: int, control: CaptureControl) -> None: ...
    def is_locked(self, control: CaptureControl) -> bool: ...
    def unlock(self, control: CaptureControl) -> None: ...
    def screenshot(self, destination: Path, control: CaptureControl) -> None: ...
    def quit(self, control: CaptureControl) -> None: ...


class AppiumSessionFactory(Protocol):
    def open(self, request: CaptureRequest) -> AppiumSession: ...
    def open_configuration(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan,
    ) -> AppiumSession: ...
    def open_export(self, request: CaptureRequest) -> AppiumSession: ...


def appium_call[T](
    operation: Callable[[], T],
    code: ErrorCode,
    message: str,
    control: CaptureControl,
    check_control: bool = True,
) -> T:
    if check_control:
        control.checkpoint()
    try:
        result = operation()
    except (WebDriverException, Urllib3HttpError) as error:
        if check_control:
            control.checkpoint()
        detail = _driver_error_message(error)
        raise CaptureAdapterError(code=code, message=f"{message}: {detail}") from error
    if check_control:
        control.checkpoint()
    return result


def _driver_error_message(error: WebDriverException | Urllib3HttpError) -> str:
    match error:
        case WebDriverException():
            return str(error.msg or error)
        case Urllib3HttpError():
            return str(error)
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class WebDriverSession:
    driver: WebDriverClient

    def reset_application(self, control: CaptureControl) -> None:
        if not isinstance(self.driver, RestartableWebDriver):
            return
        driver = cast("RestartableWebDriver", self.driver)
        _ = appium_call(
            lambda: driver.terminate_app(TRACE_BUNDLE_ID),
            ErrorCode.APPIUM_SESSION_FAILED,
            "Trace application could not be terminated before configuration",
            control,
        )
        _ = appium_call(
            lambda: driver.activate_app(TRACE_BUNDLE_ID),
            ErrorCode.APPIUM_SESSION_FAILED,
            "Trace application could not be relaunched before configuration",
            control,
        )
        self._dismiss_startup_alerts(control)

    def _dismiss_startup_alerts(self, control: CaptureControl) -> None:
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

    def configure_wallpaper(
        self,
        plan: WallpaperPlan,
        select_background: bool,
        control: CaptureControl,
        reference_date: datetime,
    ) -> None:
        ui = AppiumUI(self.driver, appium_call)
        data_driver = WallpaperDataDriver(ui)
        calendars = data_driver.provision(plan, reference_date, control)
        data_driver.open_wallpaper_editor(control)
        WallpaperStructureDriver(ui).configure(plan, calendars, control)
        WallpaperEditorDriver(self.driver, appium_call).configure(
            plan,
            select_background,
            control,
        )

    def cleanup_wallpaper(self, plan: WallpaperPlan, control: CaptureControl) -> None:
        WallpaperDataDriver(AppiumUI(self.driver, appium_call)).cleanup(plan, control)

    def configure_components(self, control: CaptureControl) -> None:
        WallpaperEditorDriver(self.driver, appium_call).save(control)

    def session_id(self, control: CaptureControl) -> str:
        session_id = appium_call(
            lambda: self.driver.session_id,
            ErrorCode.APPIUM_SESSION_FAILED,
            "Appium session identifier could not be read",
            control,
        )
        if session_id is None or not session_id:
            raise CaptureAdapterError(
                code=ErrorCode.APPIUM_SESSION_FAILED,
                message="Appium session did not expose a session identifier",
            )
        return session_id

    def lock(self, seconds: int, control: CaptureControl) -> None:
        _ = appium_call(
            lambda: self.driver.lock(seconds),
            ErrorCode.LOCK_SCREEN_UNAVAILABLE,
            "Appium could not lock the simulator",
            control,
        )

    def is_locked(self, control: CaptureControl) -> bool:
        return appium_call(
            self.driver.is_locked,
            ErrorCode.LOCK_SCREEN_UNAVAILABLE,
            "Appium could not read the simulator lock state",
            control,
        )

    def unlock(self, control: CaptureControl) -> None:
        _ = appium_call(
            self.driver.unlock,
            ErrorCode.LOCK_SCREEN_UNAVAILABLE,
            "Appium could not unlock the simulator",
            control,
        )

    def screenshot(self, destination: Path, control: CaptureControl) -> None:
        if path_has_symlink_component(destination):
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="Appium screenshot destination contains a symlink",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        saved = appium_call(
            lambda: self.driver.save_screenshot(str(destination)),
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Appium could not capture the iPhone UI",
            control,
        )
        if not saved or not destination.is_file():
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Appium reported a missing iPhone UI screenshot",
            )

    def quit(self, control: CaptureControl) -> None:
        _ = appium_call(
            self.driver.quit,
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Appium session cleanup failed",
            control,
            check_control=False,
        )


@dataclass(frozen=True, slots=True)
class DefaultAppiumSessionFactory:
    server_url: str

    def __post_init__(self) -> None:
        """Validate the local Appium trust boundary at composition time."""
        _ = validate_appium_server_url(self.server_url)

    def open(self, request: CaptureRequest) -> AppiumSession:
        return self.open_export(request)

    def open_configuration(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan,
    ) -> AppiumSession:
        return self._open(
            request,
            build_configuration_process_arguments(
                request,
                capture_request_digest(request, plan),
            ),
        )

    def open_export(self, request: CaptureRequest) -> AppiumSession:
        return self._open(request, build_component_process_arguments(request))

    def _open(self, request: CaptureRequest, process_arguments: ProcessArguments) -> AppiumSession:
        client_config = AppiumClientConfig(
            remote_server_addr=self.server_url,
            keep_alive=False,
            timeout=request.control.remaining_seconds(),
        )
        driver = appium_call(
            lambda: webdriver.Remote(
                options=build_xcuitest_options(request, process_arguments),
                client_config=client_config,
            ),
            ErrorCode.APPIUM_SESSION_FAILED,
            "Appium session could not start",
            request.control,
        )
        return WebDriverSession(driver=driver)
