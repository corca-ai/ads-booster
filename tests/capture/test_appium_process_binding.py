from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from ads_booster.capture.appium_editor_verifier import (
    AppiumProcessBinding,
    DefaultAppiumEditorVerifier,
)
from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.capture.simctl_command import CommandResult
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    import pytest


@dataclass(frozen=True, slots=True)
class SourceHttp:
    response: HttpResponse
    requested_urls: list[str]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        assert not headers
        self.requested_urls.append(url)
        return self.response


@dataclass(frozen=True, slots=True)
class RecordingProcessRunner:
    result: CommandResult
    calls: list[tuple[tuple[str, ...], float]]

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        self.calls.append((command, timeout_seconds))
        return self.result


def test_default_editor_verifier_accepts_live_process_with_exact_launch_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Appium reports a live Trace PID whose host command retains the full binding
    response = HttpResponse(200, b'{"value":"<App processId=\\"4321\\"/>"}', {})
    requested_urls: list[str] = []
    expected_arguments = (
        "-traceMarketingAutomation",
        "-traceMarketingExportWallpaper",
        "-traceMarketingRequestDigest",
        "a" * 64,
    )
    runner_calls: list[tuple[tuple[str, ...], float]] = []
    runner = RecordingProcessRunner(
        CommandResult(
            stdout=" ".join(("/simulator/Trace.app/Trace", *expected_arguments)),
            returncode=0,
        ),
        runner_calls,
    )

    def create_process_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return SourceHttp(response, requested_urls)

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_process_http,
    )

    # When the worker verifies the session immediately before Save
    binding = DefaultAppiumEditorVerifier(runner=runner).capture_process_binding(
        "http://127.0.0.1:4723",
        "session/1",
        expected_arguments,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the exact contiguous arguments bind that live process to the request
    assert binding == AppiumProcessBinding(session_id="session/1", process_id="4321")
    assert requested_urls == ["http://127.0.0.1:4723/session/session%2F1/source"]
    assert runner_calls[0][0] == ("/bin/ps", "-p", "4321", "-ww", "-o", "command=")


def test_saved_binding_reuses_ready_pid_without_reading_changed_ui_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Ready already captured the bound Trace PID before Save changes the UI hierarchy
    expected_arguments = ("-traceMarketingAutomation", "-traceMarketingExportWallpaper")
    binding = AppiumProcessBinding(session_id="session-1", process_id="4321")
    runner = RecordingProcessRunner(
        CommandResult(
            stdout=" ".join(("/simulator/Trace.app/Trace", *expected_arguments)),
            returncode=0,
        ),
        [],
    )

    def reject_source_read(read_timeout: float | None = None) -> SourceHttp:
        raise AssertionError(read_timeout)

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        reject_source_read,
    )

    # When the saved marker revalidates the same process
    verified = DefaultAppiumEditorVerifier(runner=runner).verify_process_binding(
        binding,
        expected_arguments,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then only the ready PID command is checked and no post-Save source snapshot is requested
    assert verified is True
    assert runner.calls[0][0] == ("/bin/ps", "-p", "4321", "-ww", "-o", "command=")


def test_default_editor_verifier_rejects_live_process_missing_launch_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Appium still owns a session but Trace was bundle-only relaunched without arguments
    response = HttpResponse(200, b'{"value":"<App processId=\\"4321\\"/>"}', {})
    expected_arguments = ("-traceMarketingAutomation", "-traceMarketingExportWallpaper")
    runner = RecordingProcessRunner(
        CommandResult(stdout="/simulator/Trace.app/Trace", returncode=0),
        [],
    )

    def create_relaunched_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return SourceHttp(response, [])

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_relaunched_http,
    )

    # When the worker validates the current process rather than stale session capabilities
    binding = DefaultAppiumEditorVerifier(runner=runner).capture_process_binding(
        "http://127.0.0.1:4723",
        "session-1",
        expected_arguments,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the missing runtime binding is rejected before export collection can wait
    assert binding is None
