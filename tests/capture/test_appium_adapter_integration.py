from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from trace_capture.capture.app_group_collector import parse_app_group_container
from trace_capture.capture.appium_adapter import AppiumCaptureAdapter, AppiumComponentExportAdapter
from trace_capture.capture.appium_endpoint import validate_appium_server_url
from trace_capture.capture.artifact_validation import validate_component_png
from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ExportBinding,
    UdidCaptureLeaseFactory,
)
from trace_capture.capture.readiness import CommandResult, DefaultCaptureReadiness
from trace_capture.cli.capture import build_capture_adapter
from trace_capture.contracts import DeviceKind, ErrorCode
from trace_capture.contracts.models import DeviceTarget

from .test_appium_adapter import (
    CleanupFailureSession,
    ComponentCollector,
    FailingComponentCollector,
    LockedSession,
    ScreenshotSimulator,
    SessionFactory,
    UnlockedSession,
    UnsupportedWallpaperSimulator,
    capture_request,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_validate_component_png_when_artifact_is_symlink_rejects_resolved_bytes(
    tmp_path: Path,
) -> None:
    # Given a valid component PNG exposed through a symlink
    real_artifact = tmp_path / "real.png"
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for x in range(5, 15):
        for y in range(5, 15):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(real_artifact, format="PNG")
    artifact = tmp_path / "artifact.png"
    artifact.symlink_to(real_artifact)
    binding = ExportBinding(
        request_sha256="5" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-symlink-artifact",
        cleared_at_ns=0,
    )

    # When the artifact validator receives the symlink
    with pytest.raises(CaptureAdapterError) as raised:
        _ = validate_component_png(artifact, binding, 1)

    # Then validation fails closed without resolving the symlink
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_udid_lease_when_same_simulator_is_already_captured(tmp_path: Path) -> None:
    # Given one capture already holds the simulator-scoped local lease
    lease_factory = UdidCaptureLeaseFactory(root=tmp_path / "leases")

    # When another capture requests the same simulator
    with (
        lease_factory.acquire("E1FB798D-79E6-4B25-A987-D298A4FD122A"),
        pytest.raises(CaptureAdapterError) as raised,
        lease_factory.acquire("E1FB798D-79E6-4B25-A987-D298A4FD122A"),
    ):
        pass

    # Then the overlap is rejected and a released lease can be acquired again
    assert raised.value.code is ErrorCode.CAPTURE_LEASE_UNAVAILABLE
    with lease_factory.acquire("E1FB798D-79E6-4B25-A987-D298A4FD122A"):
        pass


@pytest.mark.parametrize(
    "server_url",
    [
        "http://example.com:4723",
        "https://127.0.0.1:4723",
        "http://127.0.0.1:4723?token=secret",
        "http://user:pass@127.0.0.1:4723",
    ],
)
def test_validate_appium_server_when_endpoint_is_not_plain_loopback(
    server_url: str,
) -> None:
    # Given an Appium URL that escapes or obscures the local trust boundary
    # When the endpoint is parsed
    # Then capture fails before a WebDriver connection is attempted
    with pytest.raises(CaptureAdapterError) as raised:
        _ = validate_appium_server_url(server_url)
    assert raised.value.code is ErrorCode.APPIUM_ENDPOINT_REJECTED


def test_validate_appium_server_when_ipv6_loopback_is_used() -> None:
    # Given an explicit IPv6 loopback Appium endpoint
    # When the endpoint is parsed
    endpoint = validate_appium_server_url("http://[::1]:4723/wd/hub")

    # Then the normalized local endpoint is accepted
    assert endpoint == "http://[::1]:4723/wd/hub"


def test_adapter_when_device_locks(tmp_path: Path) -> None:
    # Given a reachable Appium session and simulator controller
    adapter = AppiumCaptureAdapter(
        session_factory=SessionFactory(session=LockedSession()),
        simulator=ScreenshotSimulator(),
    )
    request = capture_request(tmp_path)

    # When the adapter captures the requested lock screen
    adapter.capture(request)

    # Then the actual simulator surface is persisted
    assert request.destination.read_bytes() == b"real-simulator-screen"


def test_adapter_when_device_does_not_lock(tmp_path: Path) -> None:
    # Given Appium returns from lock without locking the simulator
    adapter = AppiumCaptureAdapter(
        session_factory=SessionFactory(session=UnlockedSession()),
        simulator=ScreenshotSimulator(),
    )

    # When the adapter attempts the capture
    # Then it reports a typed lock-screen failure
    with pytest.raises(CaptureAdapterError) as raised:
        _ = adapter.capture(capture_request(tmp_path))
    assert raised.value.code is ErrorCode.LOCK_SCREEN_UNAVAILABLE


def test_adapter_when_simulator_cannot_render_photo_wallpaper(tmp_path: Path) -> None:
    # Given the simulator can capture its screen but cannot render custom photo wallpaper
    adapter = AppiumCaptureAdapter(
        session_factory=SessionFactory(session=LockedSession()),
        simulator=UnsupportedWallpaperSimulator(),
    )
    request = capture_request(tmp_path)

    # When the adapter captures the diagnostic lock-screen frame
    # Then it fails closed while preserving the frame for review
    with pytest.raises(CaptureAdapterError) as raised:
        adapter.capture(request)
    assert raised.value.code is ErrorCode.LOCK_SCREEN_UNAVAILABLE
    assert request.destination.read_bytes() == b"real-simulator-screen"


def test_adapter_routing_when_physical_device_is_requested(tmp_path: Path) -> None:
    # Given a capture job explicitly targeting a physical iPhone
    adapter = build_capture_adapter(
        device_kind=DeviceKind.PHYSICAL,
        appium_server="http://127.0.0.1:4723",
    )

    # When the current host attempts to use the unsupported physical-device path
    # Then it fails before incorrectly invoking the Simulator controller
    with pytest.raises(CaptureAdapterError) as raised:
        _ = adapter.capture(capture_request(tmp_path))
    assert raised.value.code is ErrorCode.PHYSICAL_DEVICE_UNAVAILABLE


def test_adapter_routing_when_simulator_component_export_is_requested() -> None:
    # Given a capture job targeting the supported Simulator path
    # When the CLI selects its Appium adapter
    adapter = build_capture_adapter(
        device_kind=DeviceKind.SIMULATOR,
        appium_server="http://127.0.0.1:4723",
    )

    # Then it selects component export rather than the superseded lock-screen capture
    assert type(adapter) is AppiumComponentExportAdapter
    assert adapter.readiness is not None


@dataclass(slots=True)
class ReadinessCommandRunner:
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        if command == ("xcrun", "simctl", "list", "devices", "booted", "--json"):
            return CommandResult(stdout="{}", returncode=0)
        if command[:3] == ("xcrun", "simctl", "listapps"):
            return CommandResult(stdout="com.corca.Trace", returncode=0)
        return CommandResult(stdout="", returncode=0)


def test_readiness_when_simulator_and_appium_are_inactive_then_it_starts_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ReadinessCommandRunner()
    appium_ready = iter((False, True))
    popen_calls: list[tuple[object, ...]] = []

    def fake_appium_ready(_readiness: DefaultCaptureReadiness) -> bool:
        return next(appium_ready, True)

    def fake_which(_name: str) -> str:
        return "/bin/appium"

    def fake_popen(*args: object, **_kwargs: object) -> object:
        popen_calls.append(args)
        return object()

    monkeypatch.setattr(
        DefaultCaptureReadiness,
        "_appium_ready",
        fake_appium_ready,
    )
    monkeypatch.setattr("trace_capture.capture.readiness.shutil.which", fake_which)
    monkeypatch.setattr("trace_capture.capture.readiness.subprocess.Popen", fake_popen)
    readiness = DefaultCaptureReadiness(
        appium_server="http://127.0.0.1:4723",
        command_runner=runner,
        poll_interval_seconds=0.001,
    )

    readiness.ensure(
        DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
        CaptureControl.start(timeout_seconds=2),
    )

    assert ("open", "-a", "Simulator") in runner.commands
    assert ("xcrun", "simctl", "boot", "E1FB798D-79E6-4B25-A987-D298A4FD122A") in runner.commands
    assert (
        "xcrun",
        "simctl",
        "bootstatus",
        "E1FB798D-79E6-4B25-A987-D298A4FD122A",
        "-b",
    ) in runner.commands
    assert popen_calls == [(["/bin/appium", "--port", "4723"],)]


def test_component_export_adapter_when_app_group_file_is_available(
    tmp_path: Path,
) -> None:
    # Given Appium can launch Trace and the App Group collector can read its export
    adapter = AppiumComponentExportAdapter(
        session_factory=SessionFactory(session=LockedSession()),
        collector=ComponentCollector(),
    )
    request = capture_request(tmp_path)

    # When the component-only adapter runs
    _ = adapter.capture(request)

    # Then it writes the native transparent component artifact without locking iOS
    with Image.open(request.destination) as image:
        assert image.format == "PNG"


def test_component_export_adapter_when_session_is_open_then_it_captures_iphone_ui(
    tmp_path: Path,
) -> None:
    adapter = AppiumComponentExportAdapter(
        session_factory=SessionFactory(session=LockedSession()),
        collector=ComponentCollector(),
    )
    destination = tmp_path / "inputs" / "iphone-ui.png"
    request = replace(capture_request(tmp_path), iphone_ui_destination=destination)

    _ = adapter.capture(request)

    assert destination.read_bytes() == b"iphone-ui-screenshot"


def test_component_export_adapter_when_capture_and_cleanup_fail(tmp_path: Path) -> None:
    # Given export validation and Appium cleanup both fail
    adapter = AppiumComponentExportAdapter(
        session_factory=SessionFactory(session=CleanupFailureSession()),
        collector=FailingComponentCollector(),
    )

    # When the component export runs
    # Then the primary typed failure is preserved with cleanup evidence attached
    with pytest.raises(CaptureAdapterError) as raised:
        _ = adapter.capture(capture_request(tmp_path))
    assert raised.value.code is ErrorCode.EXPORT_INVALID
    assert raised.value.cleanup_error == "cleanup failure"


def test_parse_app_group_container_when_trace_group_is_present() -> None:
    # Given simctl reports multiple App Group containers
    output = "group.example.other\tfixtures/other\ngroup.ai.corca.trace\tfixtures/trace-group\n"

    # When the Trace group path is parsed
    container = parse_app_group_container(output, "group.ai.corca.trace")

    # Then the exact matching container is selected
    assert container.as_posix() == "fixtures/trace-group"
