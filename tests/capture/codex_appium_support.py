from __future__ import annotations

import stat
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.capture.appium_editor_verifier import AppiumProcessBinding
from ads_booster.capture.calendar_automation_contract import CalendarPreparation
from ads_booster.contracts import (
    CaptureProvenance,
    DeviceTarget,
)
from ads_booster.providers.codex_cli import (
    CodexAppiumJobCallbacks,
    CodexAppiumReadyState,
    CodexAppiumSavedState,
)

from .codex_appium_contract_support import V2JobInputs, v2_contract

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.capture.wallpaper_collection import WallpaperCollectionRequest
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingCodexJob:
    calls: list[str]
    result: JsonObject
    payloads: list[str] = field(default_factory=list)
    ready_state: CodexAppiumReadyState | None = None
    saved_state: CodexAppiumSavedState | None = None

    def run_appium_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
        callbacks: CodexAppiumJobCallbacks,
    ) -> JsonObject:
        del schema
        assert "CONTROL_PLANE_TOKEN" not in prompt
        assert timeout_seconds > 0
        context_path = workspace / "codex-appium-job.json"
        self.payloads.append(context_path.read_text(encoding="utf-8"))
        assert stat.S_IMODE(context_path.stat().st_mode) == 0o600
        self.calls.append("codex")
        session_id = self.result.get("session_id")
        assert isinstance(session_id, str)
        ready = self.ready_state or CodexAppiumReadyState(
            schema="trace.codex-appium-ready.v1",
            session_id=session_id,
            rendered_trace_item_titles=("Focus block",),
        )
        if not callbacks.on_ready(ready):
            return self.result
        saved = self.saved_state or CodexAppiumSavedState(
            schema="trace.codex-appium-saved.v1",
            session_id=session_id,
        )
        _ = callbacks.on_saved(saved)
        return self.result


@dataclass(frozen=True, slots=True)
class RecordingReadiness:
    calls: list[str]

    def ensure(self, device: DeviceTarget, control: CaptureControl) -> None:
        del device
        control.checkpoint()
        self.calls.append("ready")


@dataclass(frozen=True, slots=True)
class RecordingCalendarDataPort:
    calls: list[str]
    prepare_error: CaptureAdapterError | None = None
    cleanup_error: CaptureAdapterError | None = None
    preparations: list[CalendarPreparation] = field(default_factory=list)

    def prepare(
        self,
        contract: CodexAppiumJobContract,
        control: CaptureControl,
    ) -> CalendarPreparation:
        control.checkpoint()
        self.calls.append("calendar_prepare")
        if self.prepare_error is not None:
            raise self.prepare_error
        preparation = CalendarPreparation(
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            calendar_identifier="trace-calendar-identifier",
            event_count=len(contract.context.promotion_material.trace_items or ()),
            todo_calendar_namespace=(
                contract.todo_calendar_namespace
                if contract.context.promotion_material.trace_todos
                else None
            ),
            todo_calendar_identifier=(
                "trace-todo-calendar-identifier"
                if contract.context.promotion_material.trace_todos
                else None
            ),
            todo_event_count=len(contract.context.promotion_material.trace_todos),
        )
        self.preparations.append(preparation)
        return preparation

    def cleanup(
        self,
        contract: CodexAppiumJobContract,
        preparation: CalendarPreparation,
        control: CaptureControl,
    ) -> None:
        control.checkpoint()
        assert preparation.request_sha256 == contract.request_sha256
        assert preparation.calendar_namespace == contract.calendar_namespace
        self.calls.append("calendar_cleanup")
        if self.cleanup_error is not None:
            raise self.cleanup_error


@dataclass(frozen=True, slots=True)
class AcceptingEditorVerifier:
    def verify(
        self,
        appium_server: str,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        expected_todos: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del appium_server, expected_todos
        control.checkpoint()
        return ready.rendered_trace_item_titles == expected_titles

    def capture_process_binding(
        self,
        appium_server: str,
        session_id: str,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> AppiumProcessBinding | None:
        del appium_server
        control.checkpoint()
        return (
            AppiumProcessBinding(session_id=session_id, process_id="4321")
            if expected_arguments
            else None
        )

    def verify_process_binding(
        self,
        binding: AppiumProcessBinding,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del binding
        control.checkpoint()
        return bool(expected_arguments)


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
        Image.new("RGB", (20, 30), (18, 52, 86)).save(
            request.destination,
            format="PNG",
        )
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


def job_paths(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    job_root = tmp_path / "request-1"
    background = job_root / "inputs" / "background.png"
    background.parent.mkdir(parents=True)
    _ = background.write_bytes(b"verified-background")
    output = job_root / "outputs" / "wallpaper.png"
    return job_root, background, output, sha256(background.read_bytes()).hexdigest()


def completed_result() -> JsonObject:
    return {
        "status": "completed",
        "session_id": "appium-session-1",
        "session_closed": True,
        "error_code": None,
    }


__all__ = ["V2JobInputs", "v2_contract"]
