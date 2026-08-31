from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Literal, Protocol

from pydantic import Field, ValidationError

from ads_booster.capture.appium_codex_prompt import codex_appium_prompt
from ads_booster.capture.appium_editor_verifier import (
    DEFAULT_APPIUM_EDITOR_VERIFIER,
    AppiumEditorVerifier,
)
from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureLeaseFactory,
    UdidCaptureLeaseFactory,
    path_has_symlink_component,
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
from ads_booster.contracts.models import ContractModel
from ads_booster.providers.codex_cli import CodexAppiumJobCallbacks

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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


class CodexAppiumJobResult(ContractModel):
    status: Literal["completed", "failed"]
    session_id: str | None = Field(min_length=1, max_length=200)
    created_calendar_titles: tuple[str, ...] = Field(max_length=8)
    remaining_calendar_titles: tuple[str, ...] = Field(max_length=8)
    cleanup_completed: bool
    session_closed: bool
    error_code: str | None = Field(pattern=r"^[a-z0-9_]+$")


_TIME_PREFIX: Final = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d\s+(.+)$")


@dataclass(frozen=True, slots=True)
class CodexAppiumJobAdapter:
    codex: StructuredCodexJob
    simulator: SimulatorPhotoImporter
    collector: AppGroupWallpaperCollector
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
        self._validate_execution_paths(contract, job_root, background, output)
        with self.lease_factory.acquire(contract.device.udid):
            control.checkpoint()
            cleared_at_ns = [self.collector.clear(contract.device.udid, control)]
            self.simulator.import_background(contract.device.udid, background, control)
            contract_path = job_root / "codex-appium-job.json"
            write_codex_appium_job_contract(contract_path, contract)
            provenances: list[CaptureProvenance] = []
            ready_states: list[CodexAppiumReadyState] = []
            saved_states: list[CodexAppiumSavedState] = []
            collection_errors: list[CaptureAdapterError] = []
            expected_titles = _expected_trace_item_titles(contract)

            def verify_ready_editor(ready: CodexAppiumReadyState) -> bool:
                ready_states.append(ready)
                verified = self._ready_state_is_verified(
                    contract,
                    ready,
                    expected_titles,
                    control,
                )
                if verified:
                    cleared_at_ns[0] = self.collector.clear(contract.device.udid, control)
                return verified

            def collect_saved_export(saved: CodexAppiumSavedState) -> bool:
                try:
                    session_id = self._require_saved_state(
                        contract,
                        saved,
                        ready_states[-1],
                    )
                    self._require_live_process_binding(contract, session_id, control)
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
                job_root,
                control,
                verify_ready_editor,
                collect_saved_export,
            )
            if not ready_states or not self._result_matches_ready(result, ready_states[-1]):
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
            self._require_completed_result(
                contract,
                result,
                ready_states[-1],
                saved_states[0],
            )
            return provenances[0]

    def _validate_execution_paths(
        self,
        contract: CodexAppiumJobContract,
        job_root: Path,
        background: Path,
        output: Path,
    ) -> None:
        for path in (job_root, background, output):
            if not path.is_absolute() or path_has_symlink_component(path):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message="Codex Appium job paths must be absolute and symlink-free",
                )
        if not job_root.is_dir() or not output.is_relative_to(job_root):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium workspace is unavailable or output is outside it",
            )
        expected_background = job_root / contract.prepared_background.path
        if background != expected_background or not background.is_file():
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="prepared background path does not match the v2 job",
            )
        try:
            background_digest = sha256(background.read_bytes()).hexdigest()
            job_root.chmod(0o700)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="prepared background could not be verified",
            ) from error
        if background_digest != contract.prepared_background.sha256:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="prepared background digest does not match the v2 job",
            )

    def _run_codex(
        self,
        job_root: Path,
        control: CaptureControl,
        on_ready: Callable[[CodexAppiumReadyState], bool],
        on_saved: Callable[[CodexAppiumSavedState], bool],
    ) -> CodexAppiumJobResult:
        try:
            payload = self.codex.run_appium_job(
                codex_appium_prompt(),
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

    def _require_completed_result(
        self,
        contract: CodexAppiumJobContract,
        result: CodexAppiumJobResult,
        ready: CodexAppiumReadyState,
        saved: CodexAppiumSavedState,
    ) -> None:
        calendar_titles = (
            *result.created_calendar_titles,
            *result.remaining_calendar_titles,
        )
        if any(not title.startswith(contract.calendar_namespace) for title in calendar_titles):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium job returned a title outside its calendar namespace",
            )
        if result.remaining_calendar_titles or not result.cleanup_completed:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium job did not complete cleanup",
            )
        if result.status != "completed" or result.session_id is None or not result.session_closed:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=(
                    f"Codex Appium job did not complete: {result.error_code or result.status}"
                ),
            )
        if (
            result.session_id != saved.session_id
            or result.created_calendar_titles != saved.created_calendar_titles
            or saved.session_id != ready.session_id
            or saved.created_calendar_titles != ready.created_calendar_titles
        ):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium completion does not match its saved marker",
            )

    @staticmethod
    def _require_saved_state(
        contract: CodexAppiumJobContract,
        saved: CodexAppiumSavedState,
        ready: CodexAppiumReadyState,
    ) -> str:
        if any(
            not title.startswith(contract.calendar_namespace)
            for title in saved.created_calendar_titles
        ):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium saved marker returned a title outside its calendar namespace",
            )
        if (
            saved.session_id != ready.session_id
            or saved.created_calendar_titles != ready.created_calendar_titles
        ):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium saved marker does not match its ready marker",
            )
        return saved.session_id

    def _ready_state_is_verified(
        self,
        contract: CodexAppiumJobContract,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        if any(
            not title.startswith(contract.calendar_namespace)
            for title in ready.created_calendar_titles
        ):
            return False
        if ready.rendered_trace_item_titles != expected_titles:
            return False
        if not self.editor_verifier.verify(
            contract.appium_server,
            ready,
            expected_titles,
            control,
        ):
            return False
        return self.editor_verifier.verify_process_binding(
            contract.appium_server,
            ready.session_id,
            contract.launch_arguments,
            control,
        )

    def _require_live_process_binding(
        self,
        contract: CodexAppiumJobContract,
        session_id: str,
        control: CaptureControl,
    ) -> None:
        if not self.editor_verifier.verify_process_binding(
            contract.appium_server,
            session_id,
            contract.launch_arguments,
            control,
        ):
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="Trace process lost its export launch binding",
            )

    @staticmethod
    def _result_matches_ready(
        result: CodexAppiumJobResult,
        ready: CodexAppiumReadyState,
    ) -> bool:
        return (
            result.session_id == ready.session_id
            and result.created_calendar_titles == ready.created_calendar_titles
        )


def _expected_trace_item_titles(contract: CodexAppiumJobContract) -> tuple[str, ...]:
    """The titles the saved wallpaper has to show, one per requested row.

    The job now carries the title as its own field, so there is no prefix left to strip:
    a row's title is what the screen must render, whether the row is timed or all-day.
    """
    trace_items = contract.context.promotion_material.trace_items
    if trace_items is None:
        return ()
    return tuple(item.title for item in trace_items)
