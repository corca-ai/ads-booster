from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, assert_never

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.client_config import AppiumClientConfig
from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver as AppiumWebDriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support import expected_conditions as conditions
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import HTTPError as Urllib3HttpError

from trace_capture.capture.appium_endpoint import validate_appium_server_url
from trace_capture.capture.appium_process import (
    ProcessArguments,
    build_configuration_process_arguments,
    build_process_arguments,
    capture_request_digest,
)
from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    CaptureLeaseFactory,
    ComponentCollectionRequest,
    ElementFindingWebDriver,
    ExportBinding,
    UdidCaptureLeaseFactory,
    WebDriverClient,
    WebDriverElement,
    path_has_symlink_component,
)
from trace_capture.contracts import CaptureProvenance, ErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from selenium.webdriver.remote.webelement import WebElement

    from trace_capture.capture.readiness import CaptureReadiness
    from trace_capture.capture.worker import CaptureRequest


TRACE_BUNDLE_ID = "com.corca.Trace"


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
    options.use_new_wda = True
    _ = options.set_capability(
        "appium:newCommandTimeout",
        max(60, math.ceil(request.control.remaining_seconds()) + 5),
    )
    _ = options.set_capability("appium:language", request.scene.locale.split("-")[0])
    _ = options.set_capability("appium:locale", request.scene.locale.replace("-", "_"))
    _ = options.set_capability(
        "appium:processArguments",
        process_arguments or build_process_arguments(request),
    )
    _ = options.set_capability("appium:sessionName", capture_request_digest(request))
    return options


class AppiumSession(Protocol):
    def configure_components(self, items: tuple[str, ...], control: CaptureControl) -> None: ...
    def session_id(self, control: CaptureControl) -> str: ...
    def lock(self, seconds: int, control: CaptureControl) -> None: ...
    def is_locked(self, control: CaptureControl) -> bool: ...
    def unlock(self, control: CaptureControl) -> None: ...
    def screenshot(self, destination: Path, control: CaptureControl) -> None: ...
    def quit(self, control: CaptureControl) -> None: ...


class AppiumSessionFactory(Protocol):
    def open(self, request: CaptureRequest) -> AppiumSession: ...
    def open_configuration(self, request: CaptureRequest) -> AppiumSession: ...
    def open_export(self, request: CaptureRequest) -> AppiumSession: ...


class SimulatorController(Protocol):
    def supports_custom_photo_wallpaper(self) -> bool: ...

    def import_background(self, udid: str, background: Path) -> None: ...

    def capture_screen(self, udid: str, destination: Path) -> None: ...


class AppGroupComponentCollector(Protocol):
    def clear(self, udid: str, control: CaptureControl) -> int: ...

    def collect(self, request: ComponentCollectionRequest) -> CaptureProvenance: ...


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

    def configure_components(self, items: tuple[str, ...], control: CaptureControl) -> None:
        for index, item in enumerate(items):
            field = self._find_element(f"marketingCapture_item_{index}", control)
            _ = appium_call(
                field.clear,
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace component field could not be cleared",
                control,
            )
            _ = appium_call(
                lambda field=field, item=item: field.send_keys(item),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace component text could not be entered",
                control,
            )
        save = self._find_element("marketingCapture_save", control)
        _ = appium_call(
            save.click,
            ErrorCode.SCENE_CAPTURE_FAILED,
            "Trace component configuration could not be saved",
            control,
        )

    def _find_element(
        self,
        identifier: str,
        control: CaptureControl,
    ) -> WebElement | WebDriverElement:
        driver = self.driver
        if isinstance(driver, AppiumWebDriver):
            element = appium_call(
                lambda: WebDriverWait(driver, control.remaining_seconds()).until(
                    conditions.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, identifier))
                ),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace component control is unavailable",
                control,
            )
            if element is False:
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Trace component control is unavailable",
                )
            return element
        if isinstance(driver, ElementFindingWebDriver):
            return appium_call(
                lambda: driver.find_element(AppiumBy.ACCESSIBILITY_ID, identifier),
                ErrorCode.SCENE_CAPTURE_FAILED,
                "Trace component control is unavailable",
                control,
            )
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Appium session cannot configure Trace components",
        )

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
        """Reject Appium endpoints outside the local trust boundary."""
        _ = validate_appium_server_url(self.server_url)

    def open(self, request: CaptureRequest) -> AppiumSession:
        return self.open_export(request)

    def open_configuration(self, request: CaptureRequest) -> AppiumSession:
        return self._open(request, build_configuration_process_arguments())

    def open_export(self, request: CaptureRequest) -> AppiumSession:
        return self._open(request, build_process_arguments(request))

    def _open(self, request: CaptureRequest, process_arguments: ProcessArguments) -> AppiumSession:
        timeout_seconds = request.control.remaining_seconds()
        client_config = AppiumClientConfig(
            remote_server_addr=self.server_url,
            keep_alive=False,
            timeout=timeout_seconds,
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


@dataclass(frozen=True, slots=True)
class AppiumCaptureAdapter:
    session_factory: AppiumSessionFactory
    simulator: SimulatorController
    readiness: CaptureReadiness | None = None

    def capture(self, request: CaptureRequest) -> None:
        if request.background is None:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="full-screen capture requires a background image",
            )
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        self.simulator.import_background(request.device.udid, request.background)
        session = self.session_factory.open(request)
        try:
            session.lock(0, request.control)
            try:
                if not session.is_locked(request.control):
                    raise CaptureAdapterError(
                        code=ErrorCode.LOCK_SCREEN_UNAVAILABLE,
                        message="Appium returned without locking the simulator",
                    )
                self.simulator.capture_screen(request.device.udid, request.destination)
                if not self.simulator.supports_custom_photo_wallpaper():
                    raise CaptureAdapterError(
                        code=ErrorCode.LOCK_SCREEN_UNAVAILABLE,
                        message=(
                            "iOS Simulator captured a diagnostic frame but cannot "
                            "render custom photo wallpaper"
                        ),
                    )
            finally:
                session.unlock(request.control)
        finally:
            session.quit(request.control)


@dataclass(frozen=True, slots=True)
class AppiumComponentExportAdapter:
    session_factory: AppiumSessionFactory
    collector: AppGroupComponentCollector
    lease_factory: CaptureLeaseFactory = field(default_factory=UdidCaptureLeaseFactory)
    readiness: CaptureReadiness | None = None

    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        with self.lease_factory.acquire(request.device.udid):
            request.control.checkpoint()
            configuration_session = self.session_factory.open_configuration(request)
            try:
                configuration_session.configure_components(
                    request.scene.trace_data.items,
                    request.control,
                )
            finally:
                configuration_session.quit(request.control)
            request.control.checkpoint()
            cleared_at_ns = self.collector.clear(request.device.udid, request.control)
            request.control.checkpoint()
            session = self.session_factory.open_export(request)
            try:
                binding = ExportBinding(
                    request_sha256=capture_request_digest(request),
                    bundle_id=TRACE_BUNDLE_ID,
                    device_udid=request.device.udid,
                    session_id=session.session_id(request.control),
                    cleared_at_ns=cleared_at_ns,
                    export_nonce=request.capture_nonce,
                    expected_width=(
                        request.scene.component_canvas.width
                        if request.scene.component_canvas is not None
                        else None
                    ),
                    expected_height=(
                        request.scene.component_canvas.height
                        if request.scene.component_canvas is not None
                        else None
                    ),
                )
                provenance = self.collector.collect(
                    ComponentCollectionRequest(
                        udid=request.device.udid,
                        destination=request.destination,
                        binding=binding,
                        control=request.control,
                    ),
                )
            except CaptureAdapterError as primary_error:
                try:
                    session.quit(request.control)
                except CaptureAdapterError as cleanup_error:
                    raise primary_error.with_cleanup_error(
                        cleanup_error.message,
                    ) from primary_error
                raise
            else:
                session.quit(request.control)
                return provenance
