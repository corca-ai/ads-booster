from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.appium_codex import CodexAppiumJobAdapter
from ads_booster.capture.appium_codex_prompt import codex_appium_prompt, wallpaper_template
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

from .codex_appium_support import (
    AcceptingEditorVerifier,
    RecordingCalendarDataPort,
    RecordingCodexJob,
    RecordingPhotoImporter,
    RecordingWallpaperCollector,
    V2JobInputs,
    completed_result,
    job_paths,
    v2_contract,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.wallpaper_collection import WallpaperCollectionRequest
    from ads_booster.transport.json_types import JsonObject


class RecordingEditorVerifier:
    def __init__(
        self,
        visible_titles: tuple[str, ...],
        process_binding_results: tuple[bool, ...] = (True,),
    ) -> None:
        self.visible_titles: tuple[str, ...] = visible_titles
        self.expected_titles: tuple[str, ...] = ()
        self.process_binding_results: list[bool] = list(process_binding_results)
        self.expected_launch_arguments: list[tuple[str, ...]] = []

    def verify(
        self,
        appium_server: str,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        expected_todos: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del appium_server, ready
        control.checkpoint()
        self.expected_titles = expected_titles
        return all(title in self.visible_titles for title in expected_titles)

    def verify_process_binding(
        self,
        appium_server: str,
        session_id: str,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del appium_server, session_id
        control.checkpoint()
        self.expected_launch_arguments.append(expected_arguments)
        return self.process_binding_results.pop(0)


def test_codex_appium_prompt_requires_collection_acknowledgement_before_cleanup() -> None:
    prompt = codex_appium_prompt()

    ready_marker = prompt.index("codex-appium-ready.json")
    ready_verified_marker = prompt.index("codex-appium-ready-verified.json")
    saved_marker = prompt.index("codex-appium-saved.json")
    collected_marker = prompt.index("codex-appium-collected.json")
    retry_field = prompt.index("retry_allowed")

    assert ready_marker < ready_verified_marker < saved_marker
    assert ready_verified_marker < retry_field < saved_marker
    assert saved_marker < collected_marker


def test_codex_appium_job_rejects_ready_marker_missing_requested_title(
    tmp_path: Path,
) -> None:
    # Given Codex omits one requested title from its pre-save marker
    calls: list[str] = []
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        rendered_trace_item_titles=("Focus block",),
    )
    verifier = RecordingEditorVerifier(("Focus block", "Lunch"))
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result(), ready_state=ready),
        calendar=RecordingCalendarDataPort(calls),
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
    assert calls == ["calendar_prepare", "clear", "import", "codex", "calendar_cleanup"]


def test_codex_appium_job_rejects_live_source_missing_requested_title(
    tmp_path: Path,
) -> None:
    # Given the marker is complete but the independently fetched Appium source is not
    calls: list[str] = []
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-1",
        rendered_trace_item_titles=("Focus block", "Lunch"),
    )
    verifier = RecordingEditorVerifier(("Focus block",))
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result(), ready_state=ready),
        calendar=RecordingCalendarDataPort(calls),
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
    assert calls == ["calendar_prepare", "clear", "import", "codex", "calendar_cleanup"]
    assert verifier.expected_titles == ("Focus block", "Lunch")


def test_codex_appium_job_rejects_ready_when_trace_process_lost_launch_binding(
    tmp_path: Path,
) -> None:
    # Given Trace was relaunched without the immutable export-binding arguments
    calls: list[str] = []
    verifier = RecordingEditorVerifier(
        ("Focus block",),
        process_binding_results=(False,),
    )
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result()),
        calendar=RecordingCalendarDataPort(calls),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=verifier,
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When the worker verifies the live process before acknowledging Save
    with pytest.raises(CaptureAdapterError, match="not verified before save"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then Save and native collection stay blocked at the worker boundary
    assert calls == ["calendar_prepare", "clear", "import", "codex", "calendar_cleanup"]
    assert verifier.expected_launch_arguments == [contract.launch_arguments]


def test_codex_appium_job_fails_fast_when_trace_process_loses_binding_after_ready(
    tmp_path: Path,
) -> None:
    # Given the process is bound at Ready but is relaunched before the saved marker
    calls: list[str] = []
    verifier = RecordingEditorVerifier(
        ("Focus block",),
        process_binding_results=(True, False),
    )
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result()),
        calendar=RecordingCalendarDataPort(calls),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=verifier,
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When the worker receives the post-Save handshake
    with pytest.raises(
        CaptureAdapterError,
        match="lost its export launch binding",
    ) as raised:
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then collection is rejected immediately instead of polling for sixty minutes
    assert raised.value.code is ErrorCode.EXPORT_INVALID
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "calendar_cleanup",
    ]
    assert verifier.expected_launch_arguments == [
        contract.launch_arguments,
        contract.launch_arguments,
    ]


def test_codex_appium_job_collects_after_recovering_with_new_bound_ready_session(
    tmp_path: Path,
) -> None:
    # Given the first Trace session is unbound and the replacement session is bound
    calls: list[str] = []
    verifier = RecordingEditorVerifier(
        ("Focus block",),
        process_binding_results=(False, True, True),
    )

    class RecoveringCodex:
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
            rejected = callbacks.on_ready(
                CodexAppiumReadyState(
                    schema="trace.codex-appium-ready.v1",
                    session_id="unbound-session",
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            assert rejected is False
            accepted = callbacks.on_ready(
                CodexAppiumReadyState(
                    schema="trace.codex-appium-ready.v1",
                    session_id="bound-session",
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            assert accepted is True
            collected = callbacks.on_saved(
                CodexAppiumSavedState(
                    schema="trace.codex-appium-saved.v1",
                    session_id="bound-session",
                )
            )
            assert collected is True
            return {
                **completed_result(),
                "session_id": "bound-session",
            }

    adapter = CodexAppiumJobAdapter(
        codex=RecoveringCodex(),
        calendar=RecordingCalendarDataPort(calls),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=verifier,
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When the same Codex turn submits a second Ready from the replacement session
    provenance = adapter.execute(
        contract,
        job_root=job_root,
        background=background,
        output=output,
        control=CaptureControl.start(timeout_seconds=30),
    )

    # Then only the bound session reaches Save and produces accepted provenance
    assert provenance.session_id == "bound-session"
    assert provenance.native_export_binding_verified is True
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "collect",
        "calendar_cleanup",
    ]
    assert verifier.expected_launch_arguments == [
        contract.launch_arguments,
        contract.launch_arguments,
        contract.launch_arguments,
    ]


def test_codex_appium_job_collects_saved_export_before_worker_calendar_cleanup(
    tmp_path: Path,
) -> None:
    # Given the worker owns Calendar cleanup after the Codex editor turn
    calls: list[str] = []
    result = completed_result()

    class SavingCodex:
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
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            assert callbacks.on_saved(
                CodexAppiumSavedState(
                    schema="trace.codex-appium-saved.v1",
                    session_id="appium-session-1",
                )
            )
            return result

    adapter = CodexAppiumJobAdapter(
        codex=SavingCodex(),
        calendar=RecordingCalendarDataPort(calls),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When one Codex turn saves and the worker collects the native export
    provenance = adapter.execute(
        contract,
        job_root=job_root,
        background=background,
        output=output,
        control=CaptureControl.start(timeout_seconds=30),
    )

    # Then the worker-owned artifact is captured before Calendar cleanup
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "collect",
        "calendar_cleanup",
    ]
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
        calendar=RecordingCalendarDataPort(calls),
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
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "collect",
        "calendar_cleanup",
    ]


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
                    rendered_trace_item_titles=("Focus block",),
                )
            )
            collected = callbacks.on_saved(
                CodexAppiumSavedState(
                    schema="trace.codex-appium-saved.v1",
                    session_id="appium-session-1",
                )
            )
            assert collected is False
            calls.append("codex_cleanup")
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
        calendar=RecordingCalendarDataPort(calls),
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

    # Then Codex closes first and worker Calendar cleanup precedes the typed error
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "collect_failed",
        "codex_cleanup",
        "calendar_cleanup",
    ]


def test_codex_appium_job_rejects_completion_that_differs_from_saved_marker(
    tmp_path: Path,
) -> None:
    # Given the final result reports a different session than the collected saved marker
    calls: list[str] = []
    saved = CodexAppiumSavedState(
        schema="trace.codex-appium-saved.v1",
        session_id="appium-session-before-cleanup",
    )
    ready = CodexAppiumReadyState(
        schema="trace.codex-appium-ready.v1",
        session_id="appium-session-before-cleanup",
        rendered_trace_item_titles=("Focus block",),
    )
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(
            calls,
            completed_result(),
            ready_state=ready,
            saved_state=saved,
        ),
        calendar=RecordingCalendarDataPort(calls),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When completion evidence is checked against the saved evidence
    with pytest.raises(CaptureAdapterError, match="does not match its ready marker"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then the collected artifact is not returned as a successful capture
    assert calls == [
        "calendar_prepare",
        "clear",
        "import",
        "codex",
        "clear",
        "collect",
        "calendar_cleanup",
    ]


def test_wallpaper_template_is_stable_for_a_candidate_and_covers_both_shapes() -> None:
    # Given a batch of candidates
    ids = tuple(f"11111111-2222-3333-4444-{index:012d}" for index in range(40))

    # When each one's screen shape is chosen
    chosen = [wallpaper_template(candidate) for candidate in ids]

    # Then a candidate always builds the same shape. A capture that fails and comes back as
    # a different layout cannot be compared against the run that failed.
    assert chosen == [wallpaper_template(candidate) for candidate in ids]
    # And both shapes appear, because the two reference posts that reached the most people
    # were one of each: a screen with the week strip and a screen without it.
    assert set(chosen) == {"panels", "week_and_panels"}


def test_codex_appium_prompt_adds_the_week_strip_only_for_that_shape() -> None:
    # When the prompt is built for each shape
    panels = codex_appium_prompt("panels")
    week = codex_appium_prompt("week_and_panels")

    # Then only one of them asks for the strip component, and both still fix the two cells
    # so Codex has no layout left to search for
    assert "주간 캘린더" not in panels
    assert "주간 캘린더" in week
    for prompt in (panels, week):
        assert "2x1" in prompt
        assert "일정 목록" in prompt
        assert "캘린더 / 미리알림 지정" in prompt


def test_codex_appium_prompt_no_longer_demands_every_row_on_screen() -> None:
    # When the prompt is built
    prompt = codex_appium_prompt("panels")

    # Then it separates creating the rows from showing them. Demanding every requested row
    # be visible is a condition Trace cannot meet once a week overflows into a "+N" badge,
    # and it is what left a capture rebuilding the same wallpaper until it timed out.
    assert "Creating them all is required; showing them all is not." in prompt
    assert "visibly contain" not in prompt
