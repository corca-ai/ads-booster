from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from ads_booster.capture.appium_editor_verifier import DefaultAppiumEditorVerifier
from ads_booster.capture.capture_safety import CaptureControl
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


def test_default_editor_verifier_rejects_a_screen_showing_rows_nobody_requested(
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
        (),
        control,
    )
    missing_title = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        ("Focus & plan", "Dinner"),
        (),
        control,
    )

    # Then XML escaping is normalized, and a screen whose visible rows are not the rows
    # this job asked for is refused - "Lunch" is on screen but "Dinner" was requested
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
            (),
            CaptureControl.start(timeout_seconds=30),
        )
        is False
    )


def test_default_editor_verifier_accepts_a_week_that_trace_folded_behind_a_badge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a week of twenty rows, of which Trace draws four and folds the rest into "+16"
    expected = tuple(f"Row {index}" for index in range(1, 21))
    todos = ("Pay rent", "Book dentist")
    visible = ("Row 1", "Row 2", "Row 3", "Row 4", "Pay rent")
    source = "".join(f'<Text name=\\"{title}\\"/>' for title in visible)
    response = HttpResponse(
        200,
        f'{{"value":"<App name=\\"lockScreenWallpaperSave\\">{source}</App>"}}'.encode(),
        {},
    )
    http = SourceHttp(response, [])

    def create_folded_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return http

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_folded_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium/session-1",
        rendered_trace_item_titles=visible[:4],
    )

    # When the worker checks the editor before Save
    verified = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        expected,
        todos,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the folded rows do not fail the job. Requiring all twenty on screen is a
    # condition no correctly built screen can meet, and it is what left a capture
    # rebuilding the same wallpaper until it timed out.
    assert verified is True


def test_default_editor_verifier_rejects_a_claim_the_live_screen_does_not_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Codex reports a row the editor is not actually showing
    expected = tuple(f"Row {index}" for index in range(1, 21))
    todos = ()
    response = HttpResponse(
        200,
        b"".join(
            (
                b'{"value":"<App name=\\"lockScreenWallpaperSave\\">',
                b'<Text name=\\"Row 1\\"/><Text name=\\"Row 2\\"/>',
                b'<Text name=\\"Row 3\\"/></App>"}',
            )
        ),
        {},
    )
    http = SourceHttp(response, [])

    def create_missing_claim_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return http

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_missing_claim_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium/session-1",
        rendered_trace_item_titles=("Row 1", "Row 2", "Row 4"),
    )

    # When the worker checks the editor before Save
    verified = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        expected,
        todos,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then the Save gate holds: what is claimed on screen has to be on screen
    assert verified is False


def test_default_editor_verifier_rejects_a_panel_that_came_out_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given only one row rendered, which is what a cell with no calendar selected looks like
    expected = tuple(f"Row {index}" for index in range(1, 21))
    todos = ()
    response = HttpResponse(
        200,
        b'{"value":"<App name=\\"lockScreenWallpaperSave\\"><Text name=\\"Row 1\\"/></App>"}',
        {},
    )
    http = SourceHttp(response, [])

    def create_empty_panel_http(read_timeout: float | None = None) -> SourceHttp:
        assert read_timeout is not None
        return http

    monkeypatch.setattr(
        "ads_booster.capture.appium_editor_verifier.create_http_client",
        create_empty_panel_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium/session-1",
        rendered_trace_item_titles=("Row 1",),
    )

    # When the worker checks the editor before Save
    verified = DefaultAppiumEditorVerifier().verify(
        "http://127.0.0.1:4723",
        ready,
        expected,
        todos,
        CaptureControl.start(timeout_seconds=30),
    )

    # Then it is refused. A folded list and an empty panel both show fewer rows than were
    # requested; the floor is what tells them apart.
    assert verified is False
