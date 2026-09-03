from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ads_booster.capture.appium_codex_prompt import (
    WallpaperTemplate,
    codex_appium_prompt,
    wallpaper_template,
)
from ads_booster.capture.appium_codex_validation import (
    CodexAppiumJobResult,
    expected_trace_item_titles,
    rendered_titles_are_credible,
    require_completed_result,
    require_saved_state,
    result_matches_ready,
    validate_execution_paths,
)
from ads_booster.capture.appium_editor_verifier import (
    DEFAULT_APPIUM_EDITOR_VERIFIER,
    AppiumEditorVerifier,
    AppiumProcessBinding,
)
from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.calendar_lifecycle import prepared_calendar
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureLeaseFactory,
    UdidCaptureLeaseFactory,
)
from ads_booster.capture.codex_appium_job import (
    CodexAppiumJobContract,
    write_codex_appium_job_contract,
)
from ads_booster.capture.wallpaper_collection import (
    WallpaperCollectionRequest,
    WallpaperExportBinding,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode
from ads_booster.providers.codex_cli import CodexAppiumJobCallbacks

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.capture.calendar_preparation import CalendarDataPort
    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.capture.readiness import CaptureReadiness
    from ads_booster.providers.codex_cli import (
        CodexAppiumReadyState,
        CodexAppiumSavedState,
    )
    from ads_booster.transport.json_types import JsonObject


class StructuredCodexJob(Protocol):
    def run_appium_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
        callbacks: CodexAppiumJobCallbacks,
    ) -> JsonObject: ...


class SimulatorPhotoImporter(Protocol):
    def import_background(
        self,
        udid: str,
        background: Path,
        control: CaptureControl,
    ) -> None: ...


class AppGroupWallpaperCollector(Protocol):
    def clear(self, udid: str, control: CaptureControl) -> int: ...

    def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance: ...


def _wallpaper_template(contract: CodexAppiumJobContract) -> WallpaperTemplate:
    """The layout this job builds, resolved against the job's own local reference day.

    The calendar builder resolves it the same way, so the rows the worker writes and the
    layout Codex is told to build always agree on how wide a week the screen can draw.
    """
    local_reference = contract.context.reference_date.astimezone(ZoneInfo(contract.time_zone))
    return wallpaper_template(contract.identity.request_id, local_reference.date())


@dataclass(frozen=True, slots=True)
class CodexAppiumJobAdapter:
    codex: StructuredCodexJob
    simulator: SimulatorPhotoImporter
    collector: AppGroupWallpaperCollector
    calendar: CalendarDataPort
    lease_factory: CaptureLeaseFactory = field(default_factory=UdidCaptureLeaseFactory)
    readiness: CaptureReadiness | None = None
    editor_verifier: AppiumEditorVerifier = DEFAULT_APPIUM_EDITOR_VERIFIER

    def ensure_ready(self, contract: CodexAppiumJobContract, control: CaptureControl) -> None:
        _ = validate_appium_server_url(contract.appium_server)
        if self.readiness is not None:
            self.readiness.ensure(contract.device, control)

    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance:
        _ = validate_appium_server_url(contract.appium_server)
        validate_execution_paths(contract, job_root, background, output)
        with (
            self.lease_factory.acquire(contract.device.udid),
            prepared_calendar(self.calendar, contract, control),
        ):
            control.checkpoint()
            cleared_at_ns = [self.collector.clear(contract.device.udid, control)]
            self.simulator.import_background(contract.device.udid, background, control)
            contract_path = job_root / "codex-appium-job.json"
            write_codex_appium_job_contract(contract_path, contract)
            provenances: list[CaptureProvenance] = []
            ready_states: list[CodexAppiumReadyState] = []
            saved_states: list[CodexAppiumSavedState] = []
            process_bindings: list[AppiumProcessBinding] = []
            collection_errors: list[CaptureAdapterError] = []
            expected_titles = expected_trace_item_titles(contract)

            def verify_ready_editor(ready: CodexAppiumReadyState) -> bool:
                ready_states.append(ready)
                binding = self._ready_process_binding(
                    contract,
                    ready,
                    expected_titles,
                    control,
                )
                if binding is not None:
                    process_bindings.append(binding)
                    cleared_at_ns[0] = self.collector.clear(contract.device.udid, control)
                    return True
                return False

            def collect_saved_export(saved: CodexAppiumSavedState) -> bool:
                try:
                    session_id = require_saved_state(saved, ready_states[-1])
                    self._require_live_process_binding(contract, process_bindings[-1], control)
                    provenances.append(
                        self.collector.collect(
                            WallpaperCollectionRequest(
                                udid=contract.device.udid,
                                destination=output,
                                binding=WallpaperExportBinding(
                                    request_sha256=contract.request_sha256,
                                    bundle_id=contract.bundle_id,
                                    device_udid=contract.device.udid,
                                    session_id=session_id,
                                    cleared_at_ns=cleared_at_ns[0],
                                    export_nonce=contract.export_nonce,
                                ),
                                control=control,
                            )
                        )
                    )
                    saved_states.append(saved)
                except CaptureAdapterError as error:
                    collection_errors.append(error)
                    return False
                return True

            result = self._run_codex(
                contract,
                job_root,
                control,
                verify_ready_editor,
                collect_saved_export,
            )
            if not ready_states or not result_matches_ready(result, ready_states[-1]):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Codex Appium result does not match its ready marker",
                )
            if collection_errors:
                raise collection_errors[0]
            if not saved_states or not provenances:
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Codex Appium editor content was not verified before save",
                )
            require_completed_result(result, ready_states[-1], saved_states[0])
            return provenances[0]

    def _run_codex(
        self,
        contract: CodexAppiumJobContract,
        job_root: Path,
        control: CaptureControl,
        on_ready: Callable[[CodexAppiumReadyState], bool],
        on_saved: Callable[[CodexAppiumSavedState], bool],
    ) -> CodexAppiumJobResult:
        try:
            payload = self.codex.run_appium_job(
                codex_appium_prompt(_wallpaper_template(contract)),
                CodexAppiumJobResult.model_json_schema(),
                workspace=job_root,
                timeout_seconds=control.remaining_seconds(),
                callbacks=CodexAppiumJobCallbacks(
                    on_ready=on_ready,
                    on_saved=on_saved,
                ),
            )
            result_path = job_root / "codex-appium-result.json"
            _ = result_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            result_path.chmod(0o600)
            return CodexAppiumJobResult.model_validate(payload)
        except (OSError, RuntimeError, ValidationError) as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium job failed",
            ) from error

    def _ready_process_binding(
        self,
        contract: CodexAppiumJobContract,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        control: CaptureControl,
    ) -> AppiumProcessBinding | None:
        if not rendered_titles_are_credible(ready.rendered_trace_item_titles, expected_titles):
            return None
        if not self.editor_verifier.verify(
            contract.appium_server,
            ready,
            expected_titles,
            contract.context.promotion_material.trace_todos,
            control,
        ):
            return None
        return self.editor_verifier.capture_process_binding(
            contract.appium_server,
            ready.session_id,
            contract.launch_arguments,
            control,
        )

    def _require_live_process_binding(
        self,
        contract: CodexAppiumJobContract,
        binding: AppiumProcessBinding,
        control: CaptureControl,
    ) -> None:
        if not self.editor_verifier.verify_process_binding(
            binding,
            contract.launch_arguments,
            control,
        ):
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="Trace process lost its export launch binding",
            )
