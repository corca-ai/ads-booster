from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from pydantic import AwareDatetime, Field, ValidationError

from ads_booster.capture.appium_codex_prompt import codex_appium_prompt
from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.appium_process import (
    build_configuration_process_arguments,
    capture_request_digest,
)
from ads_booster.capture.appium_session import TRACE_BUNDLE_ID
from ads_booster.capture.appium_ui_data import owned_calendars
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureLeaseFactory,
    UdidCaptureLeaseFactory,
    path_has_symlink_component,
)
from ads_booster.capture.wallpaper_collection import (
    WallpaperCollectionRequest,
    WallpaperExportBinding,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode, WallpaperPlan
from ads_booster.contracts.models import ContractModel, DeviceTarget

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.capture.appium_capture import (
        AppGroupWallpaperCollector,
        SimulatorPhotoImporter,
    )
    from ads_booster.capture.readiness import CaptureReadiness
    from ads_booster.capture.worker import CaptureRequest
    from ads_booster.transport.json_types import JsonObject


class StructuredCodexJob(Protocol):
    def run_appium_job(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


class CodexAppiumJobResult(ContractModel):
    status: Literal["completed", "failed"]
    session_id: str | None = Field(min_length=1, max_length=200)
    app_group_export_seen: bool
    cleanup_completed: bool
    error_code: str | None = Field(pattern=r"^[a-z0-9_]+$")


class CodexAppiumProcessArguments(ContractModel):
    args: tuple[str, ...]
    env: dict[str, str]


class CodexAppiumJobContract(ContractModel):
    schema_version: Literal["trace.codex-appium-job.v1"]
    python_executable: str
    appium_server: str
    bundle_id: Literal["com.corca.Trace"]
    app_group_id: Literal["group.ai.corca.trace"]
    device: DeviceTarget
    background: str
    reference_date: AwareDatetime
    plan: WallpaperPlan
    process_arguments: CodexAppiumProcessArguments
    owned_calendar_titles: tuple[str, ...]
    request_sha256: str
    export_nonce: str
    export_files: tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class CodexAppiumWallpaperExportAdapter:
    codex: StructuredCodexJob
    simulator: SimulatorPhotoImporter
    collector: AppGroupWallpaperCollector
    appium_server: str
    lease_factory: CaptureLeaseFactory = field(default_factory=UdidCaptureLeaseFactory)
    readiness: CaptureReadiness | None = None
    python_executable: Path = field(default_factory=lambda: Path(sys.executable).resolve())

    _GROUP_ID: ClassVar[Literal["group.ai.corca.trace"]] = "group.ai.corca.trace"
    _EXPORT_FILES: ClassVar[tuple[str, str, str]] = (
        "trace_wallpaper.png",
        "trace_wallpaper.manifest.json",
        "trace_wallpaper.error.json",
    )

    def capture(self, request: CaptureRequest, plan: WallpaperPlan) -> CaptureProvenance:
        _ = validate_appium_server_url(self.appium_server)
        background = request.background
        if background is None:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="full wallpaper export requires a searched background image",
            )
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        with self.lease_factory.acquire(request.device.udid):
            request.control.checkpoint()
            cleared_at_ns = self.collector.clear(request.device.udid, request.control)
            self.simulator.import_background(
                request.device.udid,
                background,
                request.control,
            )
            request_sha256 = capture_request_digest(request, plan)
            workspace = self._write_contract(request, plan, request_sha256)
            try:
                payload = self.codex.run_appium_job(
                    codex_appium_prompt(),
                    CodexAppiumJobResult.model_json_schema(),
                    workspace=workspace,
                    timeout_seconds=request.control.remaining_seconds(),
                )
                result_path = workspace / "codex-appium-result.json"
                _ = result_path.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                result_path.chmod(0o600)
                result = CodexAppiumJobResult.model_validate_json(json.dumps(payload))
            except (OSError, RuntimeError, ValidationError) as error:
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Codex Appium job failed",
                ) from error
            if (
                result.status != "completed"
                or result.session_id is None
                or not result.app_group_export_seen
                or not result.cleanup_completed
            ):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message=(
                        f"Codex Appium job did not complete: {result.error_code or result.status}"
                    ),
                )
            return self.collector.collect(
                WallpaperCollectionRequest(
                    udid=request.device.udid,
                    destination=request.destination,
                    binding=WallpaperExportBinding(
                        request_sha256=request_sha256,
                        bundle_id=TRACE_BUNDLE_ID,
                        device_udid=request.device.udid,
                        session_id=result.session_id,
                        cleared_at_ns=cleared_at_ns,
                        export_nonce=request.capture_nonce,
                    ),
                    control=request.control,
                )
            )

    def _write_contract(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan,
        request_sha256: str,
    ) -> Path:
        workspace = request.destination.parent / f".codex-appium-{request.job_id}"
        if path_has_symlink_component(workspace):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium workspace contains a symlink",
            )
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        process_arguments = build_configuration_process_arguments(request, request_sha256)
        contract = CodexAppiumJobContract(
            schema_version="trace.codex-appium-job.v1",
            python_executable=str(self.python_executable),
            appium_server=self.appium_server,
            bundle_id=TRACE_BUNDLE_ID,
            app_group_id=self._GROUP_ID,
            device=request.device,
            background=str(request.background),
            reference_date=request.scene.reference_date,
            plan=plan,
            process_arguments=CodexAppiumProcessArguments(
                args=tuple(process_arguments["args"]),
                env=dict(process_arguments["env"]),
            ),
            owned_calendar_titles=tuple(calendar.title for calendar in owned_calendars(plan)),
            request_sha256=request_sha256,
            export_nonce=request.capture_nonce,
            export_files=self._EXPORT_FILES,
        )
        path = workspace / "codex-appium-job.json"
        _ = path.write_text(contract.model_dump_json(), encoding="utf-8")
        path.chmod(0o600)
        return workspace
