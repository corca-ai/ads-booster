from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import pytest

from ads_booster.capture.appium_codex import (
    CodexAppiumJobAdapter,
    DefaultAppiumEditorVerifier,
)
from ads_booster.capture.appium_codex_prompt import codex_appium_prompt
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    UdidCaptureLeaseFactory,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode
from ads_booster.providers.codex_cli import (
    CodexAppiumJobCallbacks,
    CodexAppiumReadyState,
    CodexAppiumSavedState,
)
from ads_booster.transport.http import HttpResponse

from .codex_appium_support import (
    AcceptingEditorVerifier,
    RecordingCodexJob,
    RecordingPhotoImporter,
    RecordingWallpaperCollector,
    V2JobInputs,
    completed_result,
    job_paths,
    v2_contract,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import TracebackType

    from ads_booster.capture.wallpaper_collection import WallpaperCollectionRequest
    from ads_booster.transport.json_types import JsonObject


class RecordingEditorVerifier:
    def __init__(self, visible_titles: tuple[str, ...]) -> None:
        self.visible_titles: tuple[str, ...] = visible_titles
        self.expected_titles: tuple[str, ...] = ()

    def verify(
        self,
        appium_server: str,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del appium_server, ready
        control.checkpoint()
        self.expected_titles = expected_titles
        return all(title in self.visible_titles for title in expected_titles)


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


def test_codex_appium_prompt_requires_collection_acknowledgement_before_cleanup() -> None:
    prompt = codex_appium_prompt()

    ready_marker = prompt.index("codex-appium-ready.json")
    ready_verified_marker = prompt.index("codex-appium-ready-verified.json")
    save_permission = prompt.index("Tap Save only when ready_verified")
    saved_marker = prompt.index("codex-appium-saved.json")
    collected_marker = prompt.index("codex-appium-collected.json")
    cleanup_prohibition = prompt.index("Do not delete calendars")
    cleanup_instruction = prompt.index("remove only calendars")

    assert ready_marker < ready_verified_marker < save_permission < saved_marker
    assert saved_marker < collected_marker < cleanup_prohibition < cleanup_instruction
    assert "ready_verified is false, do not tap Save" in prompt
    assert "collection_succeeded is false" in prompt


def test_codex_appium_job_rejects_ready_marker_missing_requested_title(
    tmp_path: Path,
) -> None:
    # Given Codex omits one requested title from its pre-save marker
    calls: list[str] = []
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        created_calendar_titles=("trace-request-1-calendar-1",),
        rendered_trace_item_titles=("Focus block",),
    )
    verifier = RecordingEditorVerifier(("Focus block", "Lunch"))
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result(), ready_state=ready),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=verifier,
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(
        V2JobInputs(
            background_sha256=background_sha256,
            trace_items=("09:30 Focus block", "Lunch"),
        )
    )

    # When the worker compares the marker against the immutable request
    with pytest.raises(CaptureAdapterError, match="not verified before save"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then Save collection never starts
    assert calls == ["clear", "import", "codex"]


def test_codex_appium_job_rejects_live_source_missing_requested_title(
    tmp_path: Path,
) -> None:
    # Given the marker is complete but the independently fetched Appium source is not
    calls: list[str] = []
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        created_calendar_titles=("trace-request-1-calendar-1",),
        rendered_trace_item_titles=("Focus block", "Lunch"),
    )
    verifier = RecordingEditorVerifier(("Focus block",))
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result(), ready_state=ready),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=verifier,
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(
        V2JobInputs(
            background_sha256=background_sha256,
            trace_items=("09:30 Focus block", "Lunch"),
        )
    )

    # When live source verification runs before Save
    with pytest.raises(CaptureAdapterError, match="not verified before save"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then collection remains blocked and valid HH:MM prefixes alone were stripped
    assert calls == ["clear", "import", "codex"]
    assert verifier.expected_titles == ("Focus block", "Lunch")


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
        "ads_booster.capture.appium_codex.create_http_client",
        create_source_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium/session-1",
        created_calendar_titles=("trace-request-1-calendar-1",),
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
        "ads_booster.capture.appium_codex.create_http_client",
        create_calendar_http,
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        created_calendar_titles=("trace-request-1-calendar-1",),
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


def test_codex_appium_job_collects_saved_export_before_codex_cleanup_overwrites_it(
    tmp_path: Path,
) -> None:
    # Given Trace rewrites the native export after request calendars are deleted
    calls: list[str] = []
    result = completed_result()

    class CleanupOverwritingCodex:
        export_state: str

        def __init__(self) -> None:
            self.export_state = "saved"

        def run_appium_job(
            self,
            prompt: str,
            schema: JsonObject,
            *,
            workspace: Path,
            timeout_seconds: float,
            callbacks: CodexAppiumJobCallbacks,
        ) -> JsonObject:
            del prompt, schema, workspace, timeout_seconds
            calls.append("codex")
            assert callbacks.on_ready(
                CodexAppiumReadyState(
                    schema="trace.codex-appium-ready.v1",
                    session_id="appium-session-1",
                    created_calendar_titles=("trace-request-1-calendar-1",),
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            assert callbacks.on_saved(
                CodexAppiumSavedState(
                    schema="trace.codex-appium-saved.v1",
                    session_id="appium-session-1",
                    created_calendar_titles=("trace-request-1-calendar-1",),
                )
            )
            self.export_state = "blank-after-cleanup"
            calls.append("cleanup")
            return result

    class StateAwareCollector:
        codex: CleanupOverwritingCodex

        def __init__(self, codex: CleanupOverwritingCodex) -> None:
            self.codex = codex

        def clear(self, udid: str, control: CaptureControl) -> int:
            del udid
            control.checkpoint()
            calls.append("clear")
            return 1

        def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
            assert self.codex.export_state == "saved"
            return RecordingWallpaperCollector(calls).collect(request)

    codex = CleanupOverwritingCodex()
    adapter = CodexAppiumJobAdapter(
        codex=codex,
        simulator=RecordingPhotoImporter(calls),
        collector=StateAwareCollector(codex),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When one Codex turn saves, waits for collection, then cleans up
    provenance = adapter.execute(
        contract,
        job_root=job_root,
        background=background,
        output=output,
        control=CaptureControl.start(timeout_seconds=30),
    )

    # Then the worker-owned artifact is captured before the cleanup rewrite
    assert calls == ["clear", "import", "codex", "clear", "collect", "cleanup"]
    assert provenance.native_export_binding_verified is True


def test_codex_appium_job_clears_early_exports_at_the_verified_save_boundary(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class GenerationRecordingCollector:
        clear_count: int
        collected_after_ns: int | None

        def __init__(self) -> None:
            self.clear_count = 0
            self.collected_after_ns = None

        def clear(self, udid: str, control: CaptureControl) -> int:
            del udid
            control.checkpoint()
            self.clear_count += 1
            calls.append("clear")
            return self.clear_count

        def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
            self.collected_after_ns = request.binding.cleared_at_ns
            return RecordingWallpaperCollector(calls).collect(request)

    collector = GenerationRecordingCollector()
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result()),
        simulator=RecordingPhotoImporter(calls),
        collector=collector,
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)

    _ = adapter.execute(
        v2_contract(V2JobInputs(background_sha256=background_sha256)),
        job_root=job_root,
        background=background,
        output=output,
        control=CaptureControl.start(timeout_seconds=30),
    )

    assert collector.clear_count == 2
    assert collector.collected_after_ns == 2
    assert calls == ["clear", "import", "codex", "clear", "collect"]


def test_codex_appium_job_acknowledges_collection_failure_then_propagates_it(
    tmp_path: Path,
) -> None:
    # Given native collection fails after Trace saved the requested wallpaper
    calls: list[str] = []

    class CleanupRecordingCodex:
        def run_appium_job(
            self,
            prompt: str,
            schema: JsonObject,
            *,
            workspace: Path,
            timeout_seconds: float,
            callbacks: CodexAppiumJobCallbacks,
        ) -> JsonObject:
            del prompt, schema, workspace, timeout_seconds
            calls.append("codex")
            assert callbacks.on_ready(
                CodexAppiumReadyState(
                    schema="trace.codex-appium-ready.v1",
                    session_id="appium-session-1",
                    created_calendar_titles=("trace-request-1-calendar-1",),
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            collected = callbacks.on_saved(
                CodexAppiumSavedState(
                    schema="trace.codex-appium-saved.v1",
                    session_id="appium-session-1",
                    created_calendar_titles=("trace-request-1-calendar-1",),
                )
            )
            assert collected is False
            calls.append("cleanup")
            return completed_result()

    class FailingCollector:
        def clear(self, udid: str, control: CaptureControl) -> int:
            del udid
            control.checkpoint()
            calls.append("clear")
            return 1

        def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
            del request
            calls.append("collect_failed")
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="native collection failed",
            )

    adapter = CodexAppiumJobAdapter(
        codex=CleanupRecordingCodex(),
        simulator=RecordingPhotoImporter(calls),
        collector=FailingCollector(),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When the worker reports collection failure to the still-running Codex turn
    with pytest.raises(CaptureAdapterError, match="native collection failed"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then Codex cleans up before the original typed collection error escapes
    assert calls == ["clear", "import", "codex", "clear", "collect_failed", "cleanup"]


def test_codex_appium_job_rejects_completion_that_differs_from_saved_marker(
    tmp_path: Path,
) -> None:
    # Given the final result reports a different session than the collected saved marker
    calls: list[str] = []
    saved = CodexAppiumSavedState(
        schema="trace.codex-appium-saved.v1",
        session_id="appium-session-before-cleanup",
        created_calendar_titles=("trace-request-1-calendar-1",),
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-before-cleanup",
        created_calendar_titles=("trace-request-1-calendar-1",),
        rendered_trace_item_titles=("Focus block",),
    )
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(
            calls,
            completed_result(),
            ready_state=ready,
            saved_state=saved,
        ),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When cleanup completion evidence is checked against the saved evidence
    with pytest.raises(CaptureAdapterError, match="does not match its ready marker"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then the collected artifact is not returned as a successful capture
    assert calls == ["clear", "import", "codex", "clear", "collect"]
