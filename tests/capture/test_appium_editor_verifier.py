from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from ads_booster.capture.appium_editor_verifier import DefaultAppiumEditorVerifier
from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.capture.simctl_command import CommandResult
from ads_booster.providers.codex_cli import CodexAppiumReadyState
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


def test_default_editor_verifier_requires_every_title_in_live_appium_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Appium exposes the active editor source outside the Codex sandbox
    requested_urls: list[str] = []
    response = HttpResponse(
        200,
        b"".join(
            (
                b'{"value":"<App name=\\"lockScreenWallpaperSave\\" ',
                b'label=\\"Focus &amp; plan\\"><Text name=\\"Lunch\\"/></App>"}',
            )
        ),
        {},
    )
    http = SourceHttp(response, requested_urls)

    def create_source_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return http

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_source_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium/session-1",
        rendered_trace_item_titles=("Focus & plan", "Lunch"),
    )
    control = CaptureControl.start(timeout_seconds=30)

    # When the default verifier fetches that exact session
    verified = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        ("Focus & plan", "Lunch"),
        control,
    )
    missing_title = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        ("Focus & plan", "Dinner"),
        control,
    )

    # Then XML escaping is normalized and every requested title remains mandatory
    assert verified is True
    assert missing_title is False
    assert requested_urls == [
        "http://127.0.0.1:4723/session/appium%2Fsession-1/source",
        "http://127.0.0.1:4723/session/appium%2Fsession-1/source",
    ]


def test_default_editor_verifier_rejects_requested_titles_outside_trace_wallpaper_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = HttpResponse(
        200,
        b'{"value":"<Calendar><Text name=\\"Focus block\\"/></Calendar>"}',
        {},
    )

    def create_calendar_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return SourceHttp(response, [])

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_calendar_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        rendered_trace_item_titles=("Focus block",),
    )

    assert (
        DefaultAppiumEditorVerifier().verify(
            "http://127.0.0.1:4723",
            ready,
            ("Focus block",),
            CaptureControl.start(timeout_seconds=30),
        )
        is False
    )


def test_default_editor_verifier_accepts_live_process_with_exact_launch_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Appium reports a live Trace PID whose host command retains the full binding
    response = HttpResponse(
        200,
        b'{"value":"<App processId=\\"4321\\"/>"}',
        {},
    )
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
    verified = DefaultAppiumEditorVerifier(runner=runner).verify_process_binding(
        "http://127.0.0.1:4723",
        "session/1",
        expected_arguments,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the exact contiguous arguments bind that live process to the request
    assert verified is True
    assert requested_urls == ["http://127.0.0.1:4723/session/session%2F1/source"]
    assert runner_calls[0][0] == ("/bin/ps", "-p", "4321", "-ww", "-o", "command=")


def test_default_editor_verifier_rejects_live_process_missing_launch_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Appium still owns a session but Trace was bundle-only relaunched without arguments
    response = HttpResponse(
        200,
        b'{"value":"<App processId=\\"4321\\"/>"}',
        {},
    )
    expected_arguments = (
        "-traceMarketingAutomation",
        "-traceMarketingExportWallpaper",
    )
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
    verified = DefaultAppiumEditorVerifier(runner=runner).verify_process_binding(
        "http://127.0.0.1:4723",
        "session-1",
        expected_arguments,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the missing runtime binding is rejected before export collection can wait
    assert verified is False
