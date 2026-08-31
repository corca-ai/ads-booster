from __future__ import annotations

import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.capture.calendar_automation_contract import CalendarPreparation
from ads_booster.capture.codex_appium_job import (
    CodexAppiumJobContract,
    CodexAppiumJobIdentity,
)
from ads_booster.contracts import (
    CaptureProvenance,
    DeviceKind,
    DeviceTarget,
    MarketingContextBundle,
    PersonaProfile,
    PreparedBackground,
    PromotionMaterial,
    TraceBackgroundSearchProvenance,
)
from ads_booster.providers.codex_cli import (
    CodexAppiumJobCallbacks,
    CodexAppiumReadyState,
    CodexAppiumSavedState,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
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
        control: CaptureControl,
    ) -> bool:
        del appium_server
        control.checkpoint()
        return ready.rendered_trace_item_titles == expected_titles

    def verify_process_binding(
        self,
        appium_server: str,
        session_id: str,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        del appium_server, session_id
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


@dataclass(frozen=True, slots=True)
class V2JobInputs:
    task_id: str = "task-1"
    concept: str = "Planless native capture"
    device_name: str = "iPhone 17 Pro"
    country: str = "KR"
    locale: str = "ko-KR"
    time_zone: str = "Asia/Seoul"
    background_sha256: str = "a" * 64
    export_nonce: str = "b" * 64
    calendar_namespace: str = "trace-request-1"
    trace_items: tuple[str, ...] = ("Focus block",)


_DEFAULT_V2_JOB_INPUTS = V2JobInputs()


def v2_contract(
    inputs: V2JobInputs = _DEFAULT_V2_JOB_INPUTS,
) -> CodexAppiumJobContract:
    device = DeviceTarget(
        kind=DeviceKind.SIMULATOR,
        udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        platform_version="26.0",
        device_name=inputs.device_name,
    )
    context = MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id="request-1",
        campaign_id="campaign-1",
        persona=PersonaProfile(
            persona_id="persona-1",
            country=inputs.country,
            locale=inputs.locale,
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id="promotion-1",
            concept=inputs.concept,
            background_intent="quiet Seoul desk at dawn",
            trace_items=inputs.trace_items,
        ),
        reference_date=datetime(2026, 8, 28, tzinfo=UTC),
        device=device,
    )
    return CodexAppiumJobContract(
        schema_version="trace.codex-appium-job.v2",
        identity=CodexAppiumJobIdentity(
            task_id=inputs.task_id,
            run_id="run-1",
            request_id="request-1",
            idempotency_key="hosted:task-1:request-1",
            candidate_id="candidate-1",
            candidate_revision=3,
        ),
        context=context,
        prepared_background=PreparedBackground(
            path="inputs/background.png",
            sha256=inputs.background_sha256,
            provenance=TraceBackgroundSearchProvenance(
                schema_version="trace.background-search.v1",
                artifact_path="inputs/background.png",
                artifact_sha256=inputs.background_sha256,
                query="quiet Seoul desk at dawn",
                provider="google-images",
                image_url="https://images.pexels.com/photo/1",
                source_url="https://www.pexels.com/photo/1",
            ),
        ),
        device=device,
        locale=inputs.locale,
        time_zone=inputs.time_zone,
        python_executable="/usr/bin/python3",
        appium_server="http://127.0.0.1:4723",
        bundle_id="com.corca.Trace",
        app_group_id="group.ai.corca.trace",
        calendar_namespace=inputs.calendar_namespace,
        export_nonce=inputs.export_nonce,
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
