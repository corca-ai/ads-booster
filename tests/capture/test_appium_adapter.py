from __future__ import annotations

# noqa: SIZE_OK -- session and capability fixtures share one Appium contract harness
# pyright: reportUnknownMemberType=false
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, override

import pytest
from PIL import Image
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import MaxRetryError

from ads_booster.capture.app_group_collector import CommandResult
from ads_booster.capture.appium_capture import AppiumWallpaperExportAdapter
from ads_booster.capture.appium_process import (
    build_configuration_process_arguments,
    capture_request_digest,
)
from ads_booster.capture.appium_session import (
    AppiumSession,
    DefaultAppiumSessionFactory,
    WebDriverSession,
    appium_call,
    build_xcuitest_options,
)
from ads_booster.capture.appium_sliders import WallpaperSlider, normalized_slider_value
from ads_booster.capture.appium_wallpaper import wallpaper_layout_control_value
from ads_booster.capture.artifact_validation import validate_component_png
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ComponentCollectionRequest,
)
from ads_booster.capture.simulator_photo import SimctlPhotoImporter
from ads_booster.capture.worker import CaptureRequest
from ads_booster.contracts import (
    CaptureJob,
    CaptureProvenance,
    ComponentExportManifest,
    ErrorCode,
    WallpaperCellColor,
    WallpaperCellHeight,
    WallpaperComponent,
    WallpaperEvent,
    WallpaperFontSize,
    WallpaperHeaderColor,
    WallpaperLayout,
    WallpaperPlan,
    WallpaperRow,
    WallpaperStyle,
    WallpaperTextColor,
)
from tests.capture.test_worker import JOB_JSON

if TYPE_CHECKING:
    from pathlib import Path

    from appium.options.ios import XCUITestOptions

    from ads_booster.capture.wallpaper_collection import WallpaperCollectionRequest


@dataclass(frozen=True, slots=True)
class LockedSession:
    def reset_application(self, control: CaptureControl) -> None:
        del control

    def configure_wallpaper(
        self,
        plan: WallpaperPlan,
        select_background: bool,
        control: CaptureControl,
        reference_date: datetime,
    ) -> None:
        del plan, select_background, control, reference_date

    def configure_components(
        self,
        control: CaptureControl,
    ) -> None:
        del control

    def cleanup_wallpaper(self, plan: WallpaperPlan, control: CaptureControl) -> None:
        del plan, control

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

    def open_configuration(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan | None = None,
    ) -> AppiumSession:
        del plan
        return self.open(request)

    def open_export(self, request: CaptureRequest) -> AppiumSession:
        return self.open(request)


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


@dataclass(frozen=True, slots=True)
class RecordingPhotoImporter:
    calls: list[str]

    def import_background(
        self,
        udid: str,
        background: Path,
        control: CaptureControl,
    ) -> None:
        del udid, background
        control.checkpoint()
        self.calls.append("import")


@dataclass(frozen=True, slots=True)
class RecordingWallpaperCollector:
    calls: list[str]

    def clear(self, udid: str, control: CaptureControl) -> int:
        del udid
        control.checkpoint()
        self.calls.append("clear")
        return 1

    def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
        self.calls.append("collect")
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 30), (18, 52, 86)).save(request.destination, format="PNG")
        content = request.destination.read_bytes()
        return CaptureProvenance(
            request_sha256=request.binding.request_sha256,
            artifact_sha256=sha256(content).hexdigest(),
            bundle_id=request.binding.bundle_id,
            device_udid=request.binding.device_udid,
            session_id=request.binding.session_id,
            byte_size=len(content),
            width=20,
            height=30,
            source_modified_at_ns=2,
            artifact_role="trace_wallpaper",
            native_export_nonce=request.binding.export_nonce,
            native_export_binding_verified=True,
        )


@dataclass(frozen=True, slots=True)
class RecordingWallpaperSession(LockedSession):
    calls: list[str]
    cleanup_controls: list[CaptureControl] | None = None

    @override
    def reset_application(self, control: CaptureControl) -> None:
        del control
        self.calls.append("reset")

    @override
    def configure_wallpaper(
        self,
        plan: WallpaperPlan,
        select_background: bool,
        control: CaptureControl,
        reference_date: datetime,
    ) -> None:
        del plan, control, reference_date
        assert select_background
        self.calls.append("configure")

    @override
    def session_id(self, control: CaptureControl) -> str:
        del control
        self.calls.append("session_id")
        return "wallpaper-session"

    @override
    def cleanup_wallpaper(self, plan: WallpaperPlan, control: CaptureControl) -> None:
        del plan
        self.calls.append("cleanup")
        if self.cleanup_controls is not None:
            self.cleanup_controls.append(control)

    @override
    def quit(self, control: CaptureControl) -> None:
        del control
        self.calls.append("quit")


@dataclass(frozen=True, slots=True)
class FailingWallpaperCollector(RecordingWallpaperCollector):
    @override
    def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
        del request
        self.calls.append("collect")
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="primary wallpaper export failure",
        )


@dataclass(frozen=True, slots=True)
class WallpaperCleanupFailureSession(RecordingWallpaperSession):
    @override
    def cleanup_wallpaper(self, plan: WallpaperPlan, control: CaptureControl) -> None:
        del plan, control
        self.calls.append("cleanup")
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="wallpaper cleanup failure",
        )


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingCommandRunner:
    commands: list[tuple[str, ...]]

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        return CommandResult(stdout="", returncode=0)


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


def wallpaper_plan() -> WallpaperPlan:
    event = WallpaperEvent(
        title="Design review",
        starts_at=datetime(2026, 8, 26, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 26, 2, tzinfo=UTC),
        is_all_day=False,
        color="#A855F7",
    )
    return WallpaperPlan(
        schema_version="trace.wallpaper-plan.v1",
        request_id="appium-wallpaper-01",
        time_zone="Asia/Seoul",
        background_query="purple dusk over Seoul",
        reference_ids=("reference-01",),
        style=WallpaperStyle(
            text_color=WallpaperTextColor.BLACK,
            header_color=WallpaperHeaderColor.WHITE,
            cell_color=WallpaperCellColor.PURPLE,
            font_size=WallpaperFontSize.LARGE,
            cell_opacity=47,
            cell_blur=True,
            cell_height=WallpaperCellHeight.TALL,
            allow_two_line_title=True,
            image_scale=1.4,
            image_brightness=135,
            image_blur=17,
            image_dimming=42,
        ),
        rows=(
            WallpaperRow(
                layout=WallpaperLayout.TWO_BY_ONE,
                components=(
                    WallpaperComponent(title="Morning", events=(event,)),
                    WallpaperComponent(title="Evening", events=(event,)),
                ),
            ),
        ),
    )


def all_day_wallpaper_plan() -> WallpaperPlan:
    plan = wallpaper_plan()
    event = WallpaperEvent(
        title="Launch day",
        starts_at=datetime(2026, 8, 26, tzinfo=UTC),
        ends_at=datetime(2026, 8, 27, tzinfo=UTC),
        is_all_day=True,
        color="#34C759",
    )
    return plan.model_copy(
        update={
            "rows": (
                WallpaperRow(
                    layout=WallpaperLayout.ONE_BY_ONE,
                    components=(WallpaperComponent(title="Milestone", events=(event,)),),
                ),
            ),
        },
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
    request_sha256 = capture_request_digest(request, wallpaper_plan())
    process_arguments = build_configuration_process_arguments(request, request_sha256)
    options = build_xcuitest_options(request, process_arguments)

    # Then the Appium export launch is bound to this request without fixture data
    args = process_arguments["args"]
    assert "-traceMarketingFixture" not in args
    assert "-traceMarketingFixtureItems" not in args
    assert "-traceMarketingReferenceDate" not in args
    assert "-traceMarketingSurface" not in args
    assert "-traceMarketingExportWallpaper" in args
    assert "-traceMarketingExportComponents" not in args
    assert options.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"
    assert options.use_new_wda is False
    assert "-traceMarketingRequestDigest" in args
    digest_index = args.index("-traceMarketingRequestDigest") + 1
    assert args[digest_index] == request_sha256
    nonce_index = args.index("-traceMarketingExportNonce") + 1
    assert args[nonce_index] == request.capture_nonce
    assert args[nonce_index] != request_sha256
    assert options.to_capabilities()["appium:newCommandTimeout"] >= 60
    assert options.to_capabilities()["appium:wdaLaunchTimeout"] > 0
    assert options.to_capabilities()["appium:wdaConnectionTimeout"] > 0


def test_build_process_arguments_when_plan_is_explicit_then_it_passes_only_export_binding(
    tmp_path: Path,
) -> None:
    # Given a non-default wallpaper plan whose values must be entered only through UI
    request = capture_request(tmp_path)
    plan = wallpaper_plan()

    # When the native Trace process arguments are built
    request_sha256 = capture_request_digest(request, plan)
    arguments = build_configuration_process_arguments(request, request_sha256)["args"]

    # Then launch receives only the request/export binding, never plan content
    assert arguments == [
        "-traceMarketingAutomation",
        "-traceMarketingExportWallpaper",
        "-traceMarketingRequestDigest",
        request_sha256,
        "-traceMarketingExportNonce",
        request.capture_nonce,
        "-traceMarketingDeviceUDID",
        request.device.udid,
    ]
    serialized = "\n".join(arguments)
    forbidden_values = (
        plan.request_id,
        plan.time_zone,
        plan.background_query,
        plan.rows[0].components[0].title,
        plan.rows[0].components[0].events[0].title,
        plan.rows[0].components[0].events[0].color,
        plan.style.cell_color.value,
        plan.style.font_size.value,
        str(plan.rows[0].components[0].events[0].starts_at),
    )
    assert not any(value in serialized for value in forbidden_values)
    changed_plan = plan.model_copy(update={"background_query": "amber sunrise"})
    changed_sha256 = capture_request_digest(request, changed_plan)
    changed_arguments = build_configuration_process_arguments(request, changed_sha256)["args"]
    assert changed_arguments[3] != arguments[3]


def test_wallpaper_export_adapter_when_plan_is_complete_collects_full_native_wallpaper(
    tmp_path: Path,
) -> None:
    # Given one searched background and a complete request-bound wallpaper plan
    calls: list[str] = []
    adapter = AppiumWallpaperExportAdapter(
        session_factory=SessionFactory(session=RecordingWallpaperSession(calls)),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
    )
    request = capture_request(tmp_path)

    # When the native wallpaper adapter executes the plan
    provenance = adapter.capture(request, wallpaper_plan())

    # Then request-owned UI data is removed before the Appium session quits
    assert calls == [
        "clear",
        "import",
        "reset",
        "configure",
        "session_id",
        "collect",
        "cleanup",
        "quit",
    ]
    assert provenance.artifact_role == "trace_wallpaper"
    assert request.destination.is_file()


def test_wallpaper_export_adapter_when_cleanup_runs_uses_fresh_bounded_control(
    tmp_path: Path,
) -> None:
    # Given a request control and an independently bounded cleanup policy
    calls: list[str] = []
    cleanup_controls: list[CaptureControl] = []
    adapter = AppiumWallpaperExportAdapter(
        session_factory=SessionFactory(
            session=RecordingWallpaperSession(calls, cleanup_controls),
        ),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        cleanup_timeout_seconds=0.5,
    )
    request = capture_request(tmp_path)

    # When capture completes and request-owned data is cleaned up
    _ = adapter.capture(request, wallpaper_plan())

    # Then cleanup does not reuse the request deadline or become unbounded
    assert len(cleanup_controls) == 1
    cleanup_control = cleanup_controls[0]
    assert cleanup_control is not request.control
    assert 0 < cleanup_control.remaining_seconds() <= 0.5


def test_wallpaper_export_adapter_when_cleanup_fails_after_success_fails_closed(
    tmp_path: Path,
) -> None:
    # Given a successful native export whose request-owned calendar cleanup fails
    calls: list[str] = []
    adapter = AppiumWallpaperExportAdapter(
        session_factory=SessionFactory(session=WallpaperCleanupFailureSession(calls)),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
    )

    # When the wallpaper adapter finishes collection
    with pytest.raises(CaptureAdapterError) as raised:
        _ = adapter.capture(capture_request(tmp_path), wallpaper_plan())

    # Then the cleanup failure is returned and the session still quits
    assert raised.value.message == "wallpaper cleanup failure"
    assert calls[-2:] == ["cleanup", "quit"]


def test_wallpaper_export_adapter_when_capture_and_cleanup_fail_preserves_primary(
    tmp_path: Path,
) -> None:
    # Given collection and request-owned calendar cleanup both fail
    calls: list[str] = []
    adapter = AppiumWallpaperExportAdapter(
        session_factory=SessionFactory(session=WallpaperCleanupFailureSession(calls)),
        simulator=RecordingPhotoImporter(calls),
        collector=FailingWallpaperCollector(calls),
    )

    # When the wallpaper adapter executes the capture
    with pytest.raises(CaptureAdapterError) as raised:
        _ = adapter.capture(capture_request(tmp_path), wallpaper_plan())

    # Then the primary export failure survives with cleanup evidence attached
    assert raised.value.code is ErrorCode.EXPORT_INVALID
    assert raised.value.message == "primary wallpaper export failure"
    assert raised.value.cleanup_error == "wallpaper cleanup failure"
    assert calls[-2:] == ["cleanup", "quit"]


def test_simctl_photo_importer_when_background_exists_adds_it_to_the_target_simulator(
    tmp_path: Path,
) -> None:
    # Given a searched background and an isolated Simulator command recorder
    background = tmp_path / "background.png"
    Image.new("RGB", (4, 6), (12, 24, 48)).save(background, format="PNG")
    runner = RecordingCommandRunner([])

    # When the concrete photo importer runs
    SimctlPhotoImporter(runner=runner).import_background(
        "E1FB798D-79E6-4B25-A987-D298A4FD122A",
        background,
        CaptureControl.start(30),
    )

    # Then simctl addmedia receives the exact request-owned artifact
    assert runner.commands == [
        (
            "xcrun",
            "simctl",
            "addmedia",
            "E1FB798D-79E6-4B25-A987-D298A4FD122A",
            str(background),
        )
    ]


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

    monkeypatch.setattr("ads_booster.capture.appium_session.webdriver.Remote", remote)

    # When the Appium session factory opens the client
    _ = DefaultAppiumSessionFactory(server_url="http://127.0.0.1:4723").open(request)

    # Then the client timeout is the actual positive remainder, not a rounded-up second
    assert captured_timeouts == [0.25]


def test_webdriver_session_when_configuring_wallpaper_then_it_drives_every_explicit_style() -> None:
    # Given a non-default plan represented by one quadrant row component
    calls: list[str] = []
    session = WebDriverSession(driver=RecordingWebDriver(calls, component_count=1))

    # When Appium applies the typed plan through the real Trace editor
    session.configure_wallpaper(
        wallpaper_plan(),
        select_background=True,
        control=CaptureControl.start(30),
        reference_date=datetime(2026, 8, 26, tzinfo=UTC),
    )

    # Then every preloaded component and the selected background are configured before save
    background_index = calls.index(
        "find:accessibility id:lockScreenWallpaperBackgroundPicker",
    )
    style_calls = calls[background_index - 1 :]
    assert style_calls == [
        "find_all:accessibility id:lockScreenWallpaperComponentSelect.quadrants",
        "find:accessibility id:lockScreenWallpaperBackgroundPicker",
        "click",
        "find_all:-ios predicate string:name == 'PXGGridLayout-Info'",
        "w3c_photo_tap:recording-element-0",
        "find:accessibility id:lockScreenWallpaperOptions",
        "click",
        "find:accessibility id:lockScreenWallpaperBackgroundScale",
        "set_value:0.6",
        "find:accessibility id:lockScreenWallpaperBackgroundBrightness",
        "set_value:0.675",
        "find:accessibility id:lockScreenWallpaperBackgroundBlur",
        "set_value:0.34",
        "find:accessibility id:lockScreenWallpaperBackgroundDimming",
        "set_value:0.42",
        "find:accessibility id:lockScreenWallpaperComponentCellBlur",
        "get_attribute:value",
        "click",
        "find:accessibility id:lockScreenWallpaperOptionsClose",
        "click",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentLayout",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentLayout.2X1",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentTextColor.black",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentHeaderColor",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentHeaderColor.white",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentCellColor.#9060C0",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentFontSize.large",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentCellOpacity",
        "set_value:0.47",
        "find:accessibility id:lockScreenWallpaperComponentCellHeight.tall",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentTwoLineTitle",
        "get_attribute:value",
        "click",
        "find:accessibility id:lockScreenWallpaperComponentDetailClose",
        "click",
        "find:accessibility id:lockScreenWallpaperSave",
        "click",
    ]


def test_webdriver_session_when_plan_has_timed_events_creates_data_through_ui() -> None:
    # Given a timed event plan and an empty Trace automation workspace
    calls: list[str] = []
    session = WebDriverSession(driver=RecordingWebDriver(calls, component_count=1))

    # When Appium configures the wallpaper
    session.configure_wallpaper(
        wallpaper_plan(),
        select_background=True,
        control=CaptureControl.start(30),
        reference_date=datetime(2026, 8, 26, tzinfo=UTC),
    )

    # Then request-owned calendars and events are created before wallpaper editing
    assert calls[:6] == [
        "find_all:accessibility id:settingsConnectionButton",
        "find_all:accessibility id:calendar_settingsButton",
        "find:accessibility id:calendar_settingsButton",
        "click",
        "find:accessibility id:settingsConnectionButton",
        "click",
    ]
    assert "find:accessibility id:editView_startDateInput" in calls
    assert "find:accessibility id:editView_endDateInput" in calls
    event_start = calls.index("find:accessibility id:quickAddEntryButton")
    event_end = calls.index("find:accessibility id:quickCreate_createButton", event_start) + 2
    assert calls[event_start:event_end] == [
        "find:accessibility id:quickAddEntryButton",
        "click",
        "find:accessibility id:quickCreate_textField",
        "clear",
        "set_value:2026-08-26 10:00 Design review",
        "find:accessibility id:quickCreate_expandButton",
        "click",
        "find:accessibility id:editView_titleField",
        "clear",
        "set_value:Design review",
        "find:accessibility id:editView_eventType",
        "click",
        "find:accessibility id:editView_allDayToggle",
        "get_attribute:value",
        "find:accessibility id:editView_startDateInput",
        "set_value:2026-08-26T10:00+0900",
        "find:accessibility id:editView_endDateInput",
        "set_value:2026-08-26T11:00+0900",
        "find:accessibility id:editView_calendarInput",
        "clear",
        "set_value:TraceAuto-bf82f014e8e2-1-1",
        "find:accessibility id:editView_backButton",
        "click",
        "find:accessibility id:quickCreate_createButton",
        "click",
    ]
    picker_start = calls.index(
        "find:accessibility id:lockScreenWallpaperCalendarPicker.0",
    )
    assert calls[picker_start : picker_start + 14] == [
        "find:accessibility id:lockScreenWallpaperCalendarPicker.0",
        "click",
        "find:accessibility id:lockScreenWallpaperCalendarSearch",
        "clear",
        "set_value:TraceAuto-bf82f014e8e2-1-1",
        "find:accessibility id:lockScreenWallpaperCalendar.TraceAuto-bf82f014e8e2-1-1",
        "click",
        "find:accessibility id:lockScreenWallpaperDisplayName",
        "clear",
        "set_value:Morning",
        "find:accessibility id:lockScreenWallpaperCalendarSelectionBack",
        "click",
        "find:accessibility id:lockScreenWallpaperCalendarPicker.1",
        "click",
    ]


def test_webdriver_session_when_plan_has_all_day_event_sets_date_semantics() -> None:
    # Given an all-day event with explicit request-owned dates
    calls: list[str] = []
    session = WebDriverSession(driver=RecordingWebDriver(calls, component_count=1))

    # When Appium configures the wallpaper
    session.configure_wallpaper(
        all_day_wallpaper_plan(),
        select_background=True,
        control=CaptureControl.start(30),
        reference_date=datetime(2026, 8, 26, tzinfo=UTC),
    )

    # Then the all-day toggle and date-only values are entered through stable IDs
    all_day_index = calls.index("find:accessibility id:editView_allDayToggle")
    assert calls[all_day_index : all_day_index + 7] == [
        "find:accessibility id:editView_allDayToggle",
        "get_attribute:value",
        "click",
        "find:accessibility id:editView_startDateInput",
        "set_value:2026-08-26T00:00+0900",
        "find:accessibility id:editView_endDateInput",
        "set_value:2026-08-27T00:00+0900",
    ]


def test_webdriver_session_when_all_day_dates_are_omitted_uses_reference_day() -> None:
    # Given a valid all-day event whose contract intentionally omits timestamps
    plan = all_day_wallpaper_plan()
    event = (
        plan.rows[0].components[0].events[0].model_copy(update={"starts_at": None, "ends_at": None})
    )
    plan = plan.model_copy(
        update={
            "rows": (
                WallpaperRow(
                    layout=WallpaperLayout.ONE_BY_ONE,
                    components=(WallpaperComponent(title="Milestone", events=(event,)),),
                ),
            )
        }
    )
    calls: list[str] = []
    session = WebDriverSession(driver=RecordingWebDriver(calls, component_count=1))

    # When Appium creates the event from the capture context's reference date
    session.configure_wallpaper(
        plan,
        select_background=True,
        control=CaptureControl.start(30),
        reference_date=datetime(2026, 8, 26, tzinfo=UTC),
    )

    # Then Trace receives one local all-day range through its actual date controls
    assert "set_value:2026-08-26 Launch day" in calls
    assert "set_value:2026-08-26T00:00+0900" in calls
    assert "set_value:2026-08-27T00:00+0900" in calls


def test_webdriver_session_cleanup_deletes_only_request_owned_calendars() -> None:
    # Given a saved wallpaper whose request created two uniquely named calendars
    calls: list[str] = []
    owned_identifiers = frozenset(
        {
            "settingsConnectionButton",
            "connectionCalendarEdit.TraceAuto-bf82f014e8e2-1-1",
            "connectionCalendarEdit.TraceAuto-bf82f014e8e2-2-1",
        }
    )
    session = WebDriverSession(
        driver=RecordingWebDriver(calls, existing_identifiers=owned_identifiers)
    )

    # When cleanup runs after artifact collection
    session.cleanup_wallpaper(wallpaper_plan(), CaptureControl.start(30))

    # Then only the exact request-owned calendar edit rows are deleted
    assert calls == [
        "find_all:accessibility id:settingsConnectionButton",
        "find:accessibility id:settingsConnectionButton",
        "click",
        "find:accessibility id:connectionCalendarTab",
        "click",
        "find:accessibility id:connectionCalendarSearch",
        "clear",
        "set_value:TraceAuto-bf82f014e8e2-1-1",
        "find_all:accessibility id:connectionCalendarEdit.TraceAuto-bf82f014e8e2-1-1",
        "find:accessibility id:connectionCalendarEdit.TraceAuto-bf82f014e8e2-1-1",
        "click",
        "find:accessibility id:calendarEditDelete",
        "click",
        "find:accessibility id:calendarEditDeleteConfirm",
        "click",
        "find:accessibility id:connectionCalendarSearch",
        "clear",
        "set_value:TraceAuto-bf82f014e8e2-2-1",
        "find_all:accessibility id:connectionCalendarEdit.TraceAuto-bf82f014e8e2-2-1",
        "find:accessibility id:connectionCalendarEdit.TraceAuto-bf82f014e8e2-2-1",
        "click",
        "find:accessibility id:calendarEditDelete",
        "click",
        "find:accessibility id:calendarEditDeleteConfirm",
        "click",
    ]


def test_webdriver_session_cleanup_skips_missing_request_calendar() -> None:
    # Given only one of the request-owned calendars still exists after a partial failure
    calls: list[str] = []
    owned_identifiers = frozenset(
        {
            "settingsConnectionButton",
            "connectionCalendarEdit.TraceAuto-bf82f014e8e2-2-1",
        }
    )
    session = WebDriverSession(
        driver=RecordingWebDriver(calls, existing_identifiers=owned_identifiers)
    )

    # When cleanup reconciles the request-owned calendar set
    session.cleanup_wallpaper(wallpaper_plan(), CaptureControl.start(30))

    # Then a missing calendar is skipped instead of waiting for the cleanup deadline
    assert "find:accessibility id:connectionCalendarEdit.TraceAuto-bf82f014e8e2-1-1" not in calls
    assert calls.count("find:accessibility id:calendarEditDelete") == 1


def test_webdriver_session_when_request_calendar_exists_fails_before_creation() -> None:
    # Given an existing calendar that collides with this request's owned title
    calls: list[str] = []
    session = WebDriverSession(
        driver=RecordingWebDriver(
            calls,
            existing_identifiers=frozenset(
                {"connectionCalendarEdit.TraceAuto-bf82f014e8e2-1-1"},
            ),
        ),
    )

    # When Appium begins provisioning the wallpaper plan
    with pytest.raises(CaptureAdapterError) as raised:
        session.configure_wallpaper(
            wallpaper_plan(),
            select_background=True,
            control=CaptureControl.start(30),
            reference_date=datetime(2026, 8, 26, tzinfo=UTC),
        )

    # Then it fails closed without opening the create-calendar sheet
    assert raised.value.code is ErrorCode.SCENE_CAPTURE_FAILED
    assert "find:accessibility id:connectionAddCalendar" not in calls


@pytest.mark.parametrize(
    ("layout", "control_value"),
    [
        (WallpaperLayout.ONE_BY_ONE, "1X1"),
        (WallpaperLayout.TWO_BY_ONE, "2X1"),
        (WallpaperLayout.TWO_TOP_ONE_BOTTOM, "2T1B"),
        (WallpaperLayout.TWO_BY_TWO, "2X2"),
    ],
)
def test_wallpaper_layout_control_value_when_contract_variant_is_explicit(
    layout: WallpaperLayout,
    control_value: str,
) -> None:
    # Given every layout variant accepted by WallpaperPlan
    # When the variant crosses into the existing Trace UI accessibility contract
    # Then it maps to the native LockScreenWallpaperConfig raw identifier
    assert wallpaper_layout_control_value(layout) == control_value


@pytest.mark.parametrize(
    ("slider", "value", "normalized"),
    [
        (WallpaperSlider.CELL_OPACITY, 0, "0"),
        (WallpaperSlider.CELL_OPACITY, 47, "0.47"),
        (WallpaperSlider.CELL_OPACITY, 100, "1"),
        (WallpaperSlider.IMAGE_SCALE, 0.5, "0"),
        (WallpaperSlider.IMAGE_SCALE, 1.4, "0.6"),
        (WallpaperSlider.IMAGE_SCALE, 2, "1"),
        (WallpaperSlider.IMAGE_BRIGHTNESS, 0, "0"),
        (WallpaperSlider.IMAGE_BRIGHTNESS, 135, "0.675"),
        (WallpaperSlider.IMAGE_BRIGHTNESS, 200, "1"),
        (WallpaperSlider.IMAGE_BLUR, 0, "0"),
        (WallpaperSlider.IMAGE_BLUR, 17, "0.34"),
        (WallpaperSlider.IMAGE_BLUR, 50, "1"),
        (WallpaperSlider.IMAGE_DIMMING, 0, "0"),
        (WallpaperSlider.IMAGE_DIMMING, 42, "0.42"),
        (WallpaperSlider.IMAGE_DIMMING, 100, "1"),
    ],
)
def test_normalized_slider_value_when_contract_value_is_validated(
    slider: WallpaperSlider,
    value: float,
    normalized: str,
) -> None:
    # Given validated domain boundaries and non-default wallpaper values
    # When the value crosses the XCUITest slider boundary
    # Then it becomes a stable decimal in the required zero-to-one range
    assert normalized_slider_value(slider, value) == normalized


class ClientConfigLike(Protocol):
    timeout: float | int | None


@dataclass(frozen=True, slots=True)
class RecordingWebElement:
    calls: list[str]
    element_id: str = "recording-element"

    def clear(self) -> None:
        self.calls.append("clear")

    def click(self) -> None:
        self.calls.append("click")

    def send_keys(self, value: str) -> None:
        self.calls.append(f"send_keys:{value}")

    def set_value(self, value: str) -> None:
        self.calls.append(f"set_value:{value}")

    def get_attribute(self, name: str) -> str:
        self.calls.append(f"get_attribute:{name}")
        return "0"


class RecordingWebDriver:
    calls: list[str]
    component_count: int
    existing_identifiers: frozenset[str]

    def __init__(
        self,
        calls: list[str],
        component_count: int = 0,
        existing_identifiers: frozenset[str] = frozenset(),
    ) -> None:
        self.calls = calls
        self.component_count = component_count
        self.existing_identifiers = existing_identifiers

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

    def back(self) -> None:
        self.calls.append("back")

    def find_element(self, by: str, value: str) -> RecordingWebElement:
        self.calls.append(f"find:{by}:{value}")
        return RecordingWebElement(self.calls)

    def find_elements(self, by: str, value: str) -> list[RecordingWebElement]:
        self.calls.append(f"find_all:{by}:{value}")
        if value == "name == 'PXGGridLayout-Info'":
            return [RecordingWebElement(self.calls, element_id="recording-element-0")]
        if value == "calendar_settingsButton" or value in self.existing_identifiers:
            return [RecordingWebElement(self.calls)]
        if value != "lockScreenWallpaperComponentSelect.quadrants":
            return []
        return [
            RecordingWebElement(self.calls, element_id=f"recording-element-{index}")
            for index in range(self.component_count)
        ]

    def execute_photo_asset_w3c_tap(self, element_id: str) -> None:
        self.calls.append(f"w3c_photo_tap:{element_id}")

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
