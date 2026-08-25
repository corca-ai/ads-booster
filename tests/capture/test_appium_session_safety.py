from __future__ import annotations

# pyright: reportUnknownMemberType=false
from dataclasses import replace
from typing import TYPE_CHECKING

from trace_capture.capture.appium_adapter import WebDriverSession, build_xcuitest_options
from trace_capture.capture.capture_safety import CaptureControl

from .test_appium_adapter import AdvancingClock, RecordingWebDriver, capture_request

if TYPE_CHECKING:
    from pathlib import Path


def test_webdriver_session_when_cleanup_follows_cancellation_still_quits(
    tmp_path: Path,
) -> None:
    # Given a WebDriver session whose capture control is already cancelled
    cancel_file = tmp_path / "cancel"
    _ = cancel_file.touch()
    control = CaptureControl.start(timeout_seconds=30, cancel_file=cancel_file)
    calls: list[str] = []
    session = WebDriverSession(driver=RecordingWebDriver(calls))

    # When the adapter performs mandatory session cleanup
    session.quit(control)

    # Then cleanup reaches WebDriver even though capture operations are cancelled
    assert calls == ["quit"]


def test_build_options_when_deadline_exceeds_default_keeps_session_alive(
    tmp_path: Path,
) -> None:
    # Given a capture whose collector may wait longer than Appium's default command timeout
    request = capture_request(tmp_path)
    request = replace(
        request,
        control=CaptureControl(
            expires_at=120,
            cancel_file=None,
            clock=AdvancingClock(),
        ),
    )

    # When XCUITest capabilities are built for that long-running capture
    options = build_xcuitest_options(request)

    # Then Appium keeps the session alive through the capture deadline plus cleanup margin
    assert options.to_capabilities()["appium:newCommandTimeout"] == 125
