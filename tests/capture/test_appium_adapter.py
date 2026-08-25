from __future__ import annotations

# pyright: reportUnknownMemberType=false
import base64
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, override

import pytest
from PIL import Image
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import MaxRetryError

from tests.capture.test_worker import JOB_JSON
from trace_capture.capture.appium_adapter import (
    AppiumSession,
    DefaultAppiumSessionFactory,
    WebDriverSession,
    appium_call,
    build_xcuitest_options,
)
from trace_capture.capture.appium_process import build_process_arguments, capture_request_digest
from trace_capture.capture.artifact_validation import validate_component_png
from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ComponentCollectionRequest,
)
from trace_capture.capture.worker import CaptureRequest
from trace_capture.contracts import (
    CaptureJob,
    CaptureProvenance,
    ComponentExportManifest,
    ErrorCode,
)

if TYPE_CHECKING:
    from pathlib import Path

    from appium.options.ios import XCUITestOptions


@dataclass(frozen=True, slots=True)
class LockedSession:
    def session_id(self, control: CaptureControl) -> str:
        del control
        return "appium-session-01"

    def lock(self, seconds: int, control: CaptureControl) -> None:
        del seconds, control

    def is_locked(self, control: CaptureControl) -> bool:
        del control
        return True

    def unlock(self, control: CaptureControl) -> None:
        del control

    def screenshot(self, destination: Path, control: CaptureControl) -> None:
        del control
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(b"iphone-ui-screenshot")

    def quit(self, control: CaptureControl) -> None:
        del control


@dataclass(frozen=True, slots=True)
class UnlockedSession(LockedSession):
    @override
    def is_locked(self, control: CaptureControl) -> bool:
        del control
        return False


@dataclass(frozen=True, slots=True)
class SessionFactory:
    session: AppiumSession

    def open(self, request: CaptureRequest) -> AppiumSession:
        del request
        return self.session


@dataclass(frozen=True, slots=True)
class ScreenshotSimulator:
    def supports_custom_photo_wallpaper(self) -> bool:
        return True

    def import_background(self, udid: str, background: Path) -> None:
        del udid, background

    def capture_screen(self, udid: str, destination: Path) -> None:
        del udid
        _ = destination.write_bytes(b"real-simulator-screen")


@dataclass(frozen=True, slots=True)
class ComponentCollector:
    def clear(self, udid: str, control: CaptureControl) -> int:
        del udid
        control.checkpoint()
        return 1

    def collect(self, request: ComponentCollectionRequest) -> CaptureProvenance:
        request.control.checkpoint()
        image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        for x in range(5, 15):
            for y in range(5, 15):
                image.putpixel((x, y), (255, 255, 255, 255))
        image.save(request.destination, format="PNG")
        content = request.destination.read_bytes()
        manifest = ComponentExportManifest(
            schema_version="trace.component-export-manifest.v1",
            request_sha256=request.binding.request_sha256,
            export_nonce=request.binding.export_nonce,
            bundle_id=request.binding.bundle_id,
            device_udid=request.binding.device_udid,
            role="trace_components",
            artifact_sha256=sha256(content).hexdigest(),
            width=20,
            height=20,
        )
        return validate_component_png(request.destination, request.binding, 2, manifest)


@dataclass(frozen=True, slots=True)
class FailingComponentCollector(ComponentCollector):
    @override
    def collect(self, request: ComponentCollectionRequest) -> CaptureProvenance:
        del request
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="primary export failure",
        )


@dataclass(frozen=True, slots=True)
class CleanupFailureSession(LockedSession):
    @override
    def quit(self, control: CaptureControl) -> None:
        del control
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="cleanup failure",
        )


@dataclass(frozen=True, slots=True)
class UnsupportedWallpaperSimulator(ScreenshotSimulator):
    @override
    def supports_custom_photo_wallpaper(self) -> bool:
        return False


def capture_request(tmp_path: Path) -> CaptureRequest:
    job = CaptureJob.model_validate_json(JOB_JSON)
    scene = job.scenes[0]
    background = tmp_path / "background.jpg"
    _ = background.write_bytes(b"source")
    return CaptureRequest(
        job_id=job.job_id,
        device=job.device,
        scene=scene,
        background=background,
        destination=tmp_path / "capture.png",
        control=CaptureControl.start(timeout_seconds=30),
        capture_nonce="f" * 64,
    )


def test_appium_call_when_urllib3_connection_fails_then_it_returns_typed_failure() -> None:
    # Given an Appium client that cannot connect to the local server
    control = CaptureControl.start(timeout_seconds=30)

    def operation() -> str:
        raise MaxRetryError(
            HTTPConnectionPool("127.0.0.1", 4723),
            "http://127.0.0.1:4723/session",
            OSError("connection refused"),
        )

    # When the adapter invokes the connection boundary
    with pytest.raises(CaptureAdapterError) as raised:
        _ = appium_call(
            operation,
            ErrorCode.APPIUM_SESSION_FAILED,
            "Appium session could not start",
            control,
        )

    # Then it preserves a typed failure instead of leaking a traceback
    assert raised.value.code is ErrorCode.APPIUM_SESSION_FAILED
    assert "connection refused" in raised.value.message


def test_build_options_when_scene_is_valid(tmp_path: Path) -> None:
    # Given a resolved scene with three exact Trace items
    request = capture_request(tmp_path)

    # When XCUITest capabilities are built
    process_arguments = build_process_arguments(request)
    options = build_xcuitest_options(request)

    # Then the debug fixture receives the exact items and current device target
    args = process_arguments["args"]
    encoded_items = args[args.index("-traceMarketingFixtureItems") + 1]
    assert base64.b64decode(encoded_items).decode() == '["試験","レポート","夕食"]'
    assert "-traceMarketingExportComponents" in args
    assert options.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"
    assert options.use_new_wda is True
    assert "-traceMarketingRequestDigest" in args
    digest_index = args.index("-traceMarketingRequestDigest") + 1
    assert args[digest_index] == capture_request_digest(request)
    nonce_index = args.index("-traceMarketingExportNonce") + 1
    assert args[nonce_index] == request.capture_nonce
    assert args[nonce_index] != capture_request_digest(request)
    assert options.to_capabilities()["appium:newCommandTimeout"] >= 60


def test_session_factory_when_deadline_is_subsecond_uses_remaining_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a capture with less than one second remaining
    request = capture_request(tmp_path)
    clock = AdvancingClock()
    request = replace(
        request,
        control=CaptureControl(expires_at=0.25, cancel_file=None, clock=clock),
    )
    captured_timeouts: list[float | None] = []

    def remote(
        *,
        options: XCUITestOptions,
        client_config: ClientConfigLike,
    ) -> RecordingWebDriver:
        del options
        captured_timeouts.append(client_config.timeout)
        return RecordingWebDriver([])

    monkeypatch.setattr("trace_capture.capture.appium_adapter.webdriver.Remote", remote)

    # When the Appium session factory opens the client
    _ = DefaultAppiumSessionFactory(server_url="http://127.0.0.1:4723").open(request)

    # Then the client timeout is the actual positive remainder, not a rounded-up second
    assert captured_timeouts == [0.25]


class ClientConfigLike(Protocol):
    timeout: float | int | None


class RecordingWebDriver:
    calls: list[str]

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    @property
    def session_id(self) -> str:
        self.calls.append("session_id")
        return "webdriver-session"

    def lock(self, seconds: int) -> None:
        del seconds
        self.calls.append("lock")

    def is_locked(self) -> bool:
        self.calls.append("is_locked")
        return True

    def unlock(self) -> None:
        self.calls.append("unlock")

    def save_screenshot(self, filename: str) -> bool:
        self.calls.append(f"screenshot:{filename}")
        return True

    def quit(self) -> None:
        self.calls.append("quit")


class AdvancingClock:
    monotonic_value: float
    wall_time_ns: int

    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall_time_ns = 1

    def monotonic(self) -> float:
        return self.monotonic_value

    def time_ns(self) -> int:
        return self.wall_time_ns


class ExpiringWebDriver(RecordingWebDriver):
    clock: AdvancingClock

    def __init__(self, calls: list[str], clock: AdvancingClock) -> None:
        super().__init__(calls)
        self.clock = clock

    @override
    def lock(self, seconds: int) -> None:
        super().lock(seconds)
        self.clock.monotonic_value = 2


def test_webdriver_session_when_cancelled_before_call_does_not_touch_driver(
    tmp_path: Path,
) -> None:
    # Given a WebDriver session whose shared cancellation marker is already present
    cancel_file = tmp_path / "cancel"
    _ = cancel_file.touch()
    control = CaptureControl.start(timeout_seconds=30, cancel_file=cancel_file)
    calls: list[str] = []
    driver = RecordingWebDriver(calls)
    session = WebDriverSession(driver=driver)

    # When a session operation is requested after cancellation
    with pytest.raises(CaptureAdapterError) as raised:
        session.lock(0, control)

    # Then no WebDriver call runs after cancellation
    assert raised.value.code is ErrorCode.CAPTURE_CANCELLED
    assert calls == []


def test_webdriver_session_when_call_crosses_deadline_rechecks_after_driver_call() -> None:
    # Given a clock that expires while the WebDriver call is running
    clock = AdvancingClock()
    control = CaptureControl(
        expires_at=1,
        cancel_file=None,
        clock=clock,
    )
    calls: list[str] = []
    driver = ExpiringWebDriver(calls, clock)
    session = WebDriverSession(driver=driver)

    # When the fake driver advances beyond the deadline during the operation
    with pytest.raises(CaptureAdapterError) as raised:
        session.lock(0, control)

    # Then the completed call is rejected before capture proceeds further
    assert raised.value.code is ErrorCode.CAPTURE_TIMED_OUT
    assert calls == ["lock"]
