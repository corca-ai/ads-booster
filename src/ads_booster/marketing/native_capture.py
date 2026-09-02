"""# noqa: SIZE_OK - Hosted capture request contract joins preparation and verification."""

from __future__ import annotations

import base64
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import TypeAdapter, ValidationError

from ads_booster.capture.appium_codex import CodexAppiumJobAdapter
from ads_booster.capture.calendar_preparation import SimctlEventKitCalendarDataPort
from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.codex_appium_job import (
    CodexAppiumJobContract,
    CodexAppiumJobIdentity,
)
from ads_booster.capture.codex_imagegen_ui import (
    CodexImagegenIosUiLayer,
    ImagegenIosUiArtifact,
    ImagegenIosUiCaptureRequest,
)
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.capture.simulator_photo import SimctlPhotoImporter
from ads_booster.capture.wallpaper_collection import SimctlAppGroupWallpaperCollector
from ads_booster.capture.wallpaper_validation import read_wallpaper_export_manifest
from ads_booster.contracts import CaptureProvenance, PreparedBackground
from ads_booster.contracts.feedback import FeedbackContext, feedback_context_sha256
from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import DeviceKind, DeviceTarget, TraceScheduleItem
from ads_booster.marketing.background import HostedBackgroundPreparer
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskResult, TaskStatus
from ads_booster.providers.codex_background_judge import CodexBackgroundJudge
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable
from ads_booster.search.image.background import ImageSearchBackgroundFetcher
from ads_booster.search.image.providers import create_image_search_provider
from ads_booster.transport.json_types import JsonObject, JsonValue
from ads_booster.workspace.models import CandidateBackgroundSubject

if TYPE_CHECKING:
    from ads_booster.transport.http import HttpClient

_PIPELINE: Final = "hosted_workspace_capture_v1"
_MAX_IMAGE_BYTES: Final = 16 * 1024 * 1024
_LOCALES: Final = {
    "BR": "pt-BR",
    "DE": "de-DE",
    "FR": "fr-FR",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "TW": "zh-TW",
    "US": "en-US",
}
_TIME_ZONES: Final = {
    "BR": "America/Sao_Paulo",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "TW": "Asia/Taipei",
    "US": "America/New_York",
}
_DEFAULT_APPIUM_SERVER: Final = "http://127.0.0.1:4723"
_DEFAULT_TIMEOUT_SECONDS: Final = 3600.0
_RUNTIME_VERSION = re.compile(r"\.iOS-(\d+)-(\d+)$")
_MAX_TRACE_ITEMS: Final = 24
# Matches the generation contract's ceiling for the field, so a query that generation
# accepted is never dropped here for being too long.
_SEARCH_QUERY_MAX: Final = 200
# The vocabulary `background_intent` is composed from, so the fallback can strip the token
# back off before it reaches an image index.
_BACKGROUND_SUBJECT_TOKENS: Final = frozenset(
    subject.value for subject in CandidateBackgroundSubject
)
_MAX_TRACE_TODOS: Final = 20
_MAX_REFERENCE_IDS: Final = 16
_MAX_TRACE_ITEM_LENGTH: Final = 80
_MAX_TRACE_TODO_LENGTH: Final = 60
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_TRACE_ITEMS: TypeAdapter[tuple[TraceScheduleItem, ...]] = TypeAdapter(
    tuple[TraceScheduleItem, ...]
)
_TRACE_TODOS: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])
_REFERENCE_IDS: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])


def build_hosted_capture_executor(
    home: Path,
    http: HttpClient,
) -> HostedWorkspaceCaptureExecutor:
    executable = resolve_codex_executable()
    if executable is None:
        raise MarketingExecutionError("codex_exec_unavailable")
    appium_server = os.environ.get("TRACE_AGENT_APPIUM_SERVER", _DEFAULT_APPIUM_SERVER)
    timeout_seconds = _positive_timeout(
        os.environ.get("TRACE_AGENT_GENERATION_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
    )
    codex = CodexCli(executable=executable, timeout_seconds=timeout_seconds)
    return HostedWorkspaceCaptureExecutor(
        background_preparer=HostedBackgroundPreparer(
            ImageSearchBackgroundFetcher(
                image_search=create_image_search_provider(
                    http=http,
                    provider_name=os.environ.get("TRACE_AGENT_WEB_SEARCH_PROVIDER", "auto"),
                    timeout_seconds=_positive_timeout(
                        os.environ.get("TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "30")
                    ),
                ),
                http=http,
                # Geometry alone cannot see what an image is, and a composed poster is cut
                # to exactly the phone's proportions, so it wins the ranking on shape.
                # Off until somebody has watched it run. It fails closed - a judge that
                # cannot see the images rejects every row - so defaulting it on would turn
                # one unverified capability into no backgrounds at all.
                # TRACE_AGENT_BACKGROUND_JUDGE=on enables it.
                judge=(
                    CodexBackgroundJudge(codex=codex, http=http)
                    if os.environ.get("TRACE_AGENT_BACKGROUND_JUDGE", "off").lower() == "on"
                    else None
                ),
            )
        ),
        appium=CodexAppiumJobAdapter(
            codex=codex,
            simulator=SimctlPhotoImporter(),
            collector=SimctlAppGroupWallpaperCollector(),
            calendar=SimctlEventKitCalendarDataPort(),
            readiness=DefaultCaptureReadiness(appium_server=appium_server),
        ),
        output_root=home / "generated",
        ios_ui=CodexImagegenIosUiLayer(
            executable=executable,
            reference_image=_ios_ui_reference_path(),
        ),
        appium_server=appium_server,
        timeout_seconds=timeout_seconds,
    )


class DeviceResolver(Protocol):
    def resolve(self) -> DeviceTarget: ...


class BackgroundPreparer(Protocol):
    def prepare(
        self,
        bundle: MarketingContextBundle,
        job_root: Path,
    ) -> PreparedBackground: ...


class AppiumJobPort(Protocol):
    def ensure_ready(self, contract: CodexAppiumJobContract, control: CaptureControl) -> None: ...

    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance: ...


class ImagegenIosUiPort(Protocol):
    def capture(self, request: ImagegenIosUiCaptureRequest) -> ImagegenIosUiArtifact: ...


def _ios_ui_reference_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "assets" / "ios-lock-screen-date-time-reference.png"


@dataclass(frozen=True, slots=True)
class SimctlDeviceResolver:
    """Resolve a usable Simulator at execution time instead of binding one Mac UDID."""

    preferred_udid: str | None = None

    def resolve(self) -> DeviceTarget:
        try:
            completed = subprocess.run(
                ("/usr/bin/xcrun", "simctl", "list", "devices", "available", "--json"),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MarketingExecutionError("native_simulator_environment_unavailable") from error
        if completed.returncode != 0:
            raise MarketingExecutionError("native_simulator_environment_unavailable")
        try:
            payload = _JSON_OBJECT.validate_json(completed.stdout)
        except ValidationError as error:
            raise MarketingExecutionError("native_simulator_inventory_invalid") from error
        try:
            devices = _JSON_OBJECT.validate_python(payload.get("devices"))
        except ValidationError as error:
            raise MarketingExecutionError("native_simulator_inventory_invalid") from error
        if not devices:
            raise MarketingExecutionError("native_simulator_inventory_invalid")
        requested = self.preferred_udid or os.environ.get("TRACE_AGENT_DEVICE_UDID")
        candidates = _simulator_candidates(devices, requested)
        if not candidates:
            code = "native_simulator_not_found" if requested else "native_simulator_unavailable"
            raise MarketingExecutionError(code)
        _booted, version, selected = max(
            candidates,
            key=lambda value: (value[0], tuple(map(int, value[1].split(".")))),
        )
        return DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid=str(selected["udid"]),
            platform_version=version,
            device_name=str(selected["name"]),
        )


@dataclass(frozen=True, slots=True)
class PreparedCodexAppiumJob:
    task: MarketingTask
    contract: CodexAppiumJobContract
    execution_admission: ExecutionAdmission
    job_root: Path
    background: Path
    output: Path
    control: CaptureControl


@dataclass(frozen=True, slots=True)
class _CapturedPng:
    data: bytes
    digest: str
    capture_source: str
    artifact_role: str
    source_trace_artifact_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class HostedWorkspaceCaptureExecutor:
    background_preparer: BackgroundPreparer
    appium: AppiumJobPort
    output_root: Path
    ios_ui: ImagegenIosUiPort | None = None
    device_resolver: DeviceResolver = SimctlDeviceResolver()
    appium_server: str = _DEFAULT_APPIUM_SERVER
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedCodexAppiumJob:
        if task.payload.get("pipeline") != _PIPELINE:
            raise MarketingExecutionError("unsupported_hosted_capture_pipeline")
        request_id = _task_identifier(task.task_id)
        workspace_id = _required_text(task.payload, "workspace_id", 128)
        device = self.device_resolver.resolve()
        bundle = _context_bundle(task, request_id, device)
        job_root = (self.output_root / request_id).resolve()
        root = self.output_root.resolve()
        if not job_root.is_relative_to(root):
            raise MarketingExecutionError("native_capture_artifact_missing")
        try:
            job_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            job_root.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("native_capture_workspace_unavailable") from error
        prepared_background = self.background_preparer.prepare(bundle, job_root)
        background = (job_root / prepared_background.path).resolve()
        output = (job_root / "outputs" / "trace_wallpaper.png").resolve()
        contract = CodexAppiumJobContract(
            schema_version="trace.codex-appium-job.v2",
            identity=CodexAppiumJobIdentity(
                task_id=request_id,
                run_id=_task_identifier(task.run_id),
                request_id=request_id,
                idempotency_key=task.idempotency_key,
                candidate_id=_required_text(task.payload, "candidate_id", 80),
                candidate_revision=_required_integer(task.payload, "candidate_revision"),
            ),
            context=bundle,
            prepared_background=prepared_background,
            python_executable=sys.executable,
            appium_server=self.appium_server,
            bundle_id="com.corca.Trace",
            app_group_id="group.ai.corca.trace",
            device=device,
            locale=bundle.persona.locale,
            time_zone=_TIME_ZONES[bundle.persona.country],
            calendar_namespace=f"trace-{request_id}",
            export_nonce=secrets.token_hex(32),
        )
        control = CaptureControl.start(self.timeout_seconds)
        try:
            self.appium.ensure_ready(contract, control)
        except CaptureAdapterError as error:
            raise MarketingExecutionError(f"native_capture_{error.code.value}") from error
        return PreparedCodexAppiumJob(
            task=task,
            contract=contract,
            execution_admission=ExecutionAdmission(
                job_digest=contract.request_sha256,
                export_nonce=contract.export_nonce,
                workspace_id=workspace_id,
            ),
            job_root=job_root,
            background=background,
            output=output,
            control=control,
        )

    def execute(self, prepared: PreparedCodexAppiumJob) -> TaskResult:
        try:
            background_digest = sha256(prepared.background.read_bytes()).hexdigest()
        except OSError as error:
            raise MarketingExecutionError(
                "native_capture_background_missing",
                unknown_side_effect=True,
            ) from error
        if background_digest != prepared.contract.prepared_background.sha256:
            raise MarketingExecutionError(
                "native_capture_background_digest_mismatch",
                unknown_side_effect=True,
            )
        try:
            provenance = self.appium.execute(
                prepared.contract,
                job_root=prepared.job_root,
                background=prepared.background,
                output=prepared.output,
                control=prepared.control,
            )
        except CaptureAdapterError as error:
            raise MarketingExecutionError(
                f"native_capture_{error.code.value}",
                unknown_side_effect=True,
            ) from error
        trace_image = _read_trace_wallpaper(prepared, provenance, self.output_root)
        if self.ios_ui is None:
            return _capture_result(prepared.contract, trace_image)
        return _capture_result(
            prepared.contract,
            _capture_imagegen_ios_ui(prepared, trace_image, self.ios_ui),
        )


def _capture_result(
    contract: CodexAppiumJobContract,
    image: _CapturedPng,
) -> TaskResult:
    return TaskResult(
        status=TaskStatus.SUCCEEDED,
        output={
            "pipeline": _PIPELINE,
            "candidate_id": contract.identity.candidate_id,
            "candidate_revision": contract.identity.candidate_revision,
            "content_type": "image/png",
            "image_sha256": image.digest,
            "image_base64": base64.b64encode(image.data).decode("ascii"),
            "capture_source": image.capture_source,
            "native_image_sha256": image.source_trace_artifact_sha256 or image.digest,
            "image_postprocess_source": (
                image.capture_source if image.source_trace_artifact_sha256 is not None else "none"
            ),
            "artifact_role": image.artifact_role,
            "source_trace_artifact_sha256": image.source_trace_artifact_sha256,
            "native_export_binding_verified": True,
            "imagegen_ui_layer_verified": image.capture_source == "imagen_ios_ui",
            "feedback_application_sha256": contract.context.feedback_context_sha256,
        },
    )


def _read_trace_wallpaper(
    prepared: PreparedCodexAppiumJob,
    provenance: CaptureProvenance,
    output_root: Path,
) -> _CapturedPng:
    image_path = prepared.output.resolve()
    root = output_root.resolve()
    if (
        image_path != prepared.output
        or not image_path.is_relative_to(root)
        or not image_path.is_file()
    ):
        raise MarketingExecutionError(
            "native_capture_artifact_missing",
            unknown_side_effect=True,
        )
    try:
        image = image_path.read_bytes()
        manifest = read_wallpaper_export_manifest(image_path.with_suffix(".manifest.json"))
    except (OSError, CaptureAdapterError) as error:
        raise MarketingExecutionError(
            "native_capture_artifact_missing",
            unknown_side_effect=True,
        ) from error
    if not image or len(image) > _MAX_IMAGE_BYTES:
        raise MarketingExecutionError(
            "native_capture_artifact_size_invalid",
            unknown_side_effect=True,
        )
    digest = sha256(image).hexdigest()
    contract = prepared.contract
    if (
        provenance.source != "native_appium"
        or provenance.artifact_role != "trace_wallpaper"
        or not provenance.native_export_binding_verified
        or provenance.request_sha256 != contract.request_sha256
        or provenance.native_export_nonce != contract.export_nonce
        or provenance.bundle_id != contract.bundle_id
        or provenance.device_udid != contract.device.udid
    ):
        raise MarketingExecutionError(
            "native_capture_provenance_unverified",
            unknown_side_effect=True,
        )
    if (
        provenance.artifact_sha256 != digest
        or provenance.byte_size != len(image)
        or manifest.artifact_sha256 != digest
        or manifest.request_sha256 != contract.request_sha256
        or manifest.export_nonce != contract.export_nonce
        or manifest.bundle_id != contract.bundle_id
        or manifest.device_udid != contract.device.udid
        or (manifest.width, manifest.height) != (provenance.width, provenance.height)
    ):
        raise MarketingExecutionError(
            "native_capture_artifact_digest_mismatch",
            unknown_side_effect=True,
        )
    return _CapturedPng(
        data=image,
        digest=digest,
        capture_source="native_appium",
        artifact_role="trace_wallpaper",
    )


def _capture_imagegen_ios_ui(
    prepared: PreparedCodexAppiumJob,
    trace_image: _CapturedPng,
    ios_ui: ImagegenIosUiPort,
) -> _CapturedPng:
    final_path = prepared.output.with_name("imagen_ios_ui.png")
    contract = prepared.contract
    try:
        derived = ios_ui.capture(
            ImagegenIosUiCaptureRequest(
                context=contract.context,
                source_trace_wallpaper=prepared.output,
                destination=final_path,
                request_sha256=contract.request_sha256,
                export_nonce=contract.export_nonce,
                control=prepared.control,
            )
        )
    except CaptureAdapterError as error:
        raise MarketingExecutionError(
            f"native_capture_{error.code.value}",
            unknown_side_effect=True,
        ) from error
    final_image = _read_derived_image(final_path)
    final_digest = sha256(final_image).hexdigest()
    _validate_derived_artifact(
        artifact=derived,
        final_digest=final_digest,
        final_size=len(final_image),
        source_trace_digest=trace_image.digest,
        contract=contract,
    )
    return _CapturedPng(
        data=final_image,
        digest=final_digest,
        capture_source="imagen_ios_ui",
        artifact_role="imagen_ios_ui",
        source_trace_artifact_sha256=trace_image.digest,
    )


def _read_derived_image(path: Path) -> bytes:
    try:
        image = path.read_bytes()
    except OSError as error:
        raise MarketingExecutionError(
            "native_capture_artifact_missing",
            unknown_side_effect=True,
        ) from error
    if not image or len(image) > _MAX_IMAGE_BYTES:
        raise MarketingExecutionError(
            "native_capture_artifact_size_invalid",
            unknown_side_effect=True,
        )
    return image


def _validate_derived_artifact(
    *,
    artifact: ImagegenIosUiArtifact,
    final_digest: str,
    final_size: int,
    source_trace_digest: str,
    contract: CodexAppiumJobContract,
) -> None:
    manifest = artifact.manifest
    layer_digest = sha256(artifact.ui_layer_path.read_bytes()).hexdigest()
    if (
        not artifact.ui_layer_path.is_file()
        or manifest.request_sha256 != contract.request_sha256
        or manifest.export_nonce != contract.export_nonce
        or manifest.device_udid != contract.device.udid
        or manifest.source_trace_artifact_sha256 != source_trace_digest
        or manifest.imagegen_ui_layer_sha256 != layer_digest
        or manifest.artifact_sha256 != final_digest
        or manifest.width <= 0
        or manifest.height <= 0
        or final_size <= 0
    ):
        raise MarketingExecutionError(
            "native_capture_provenance_unverified",
            unknown_side_effect=True,
        )


def _simulator_candidates(
    devices: JsonObject,
    requested: str | None,
) -> list[tuple[bool, str, JsonObject]]:
    candidates: list[tuple[bool, str, JsonObject]] = []
    for runtime, entries in devices.items():
        match = _RUNTIME_VERSION.search(str(runtime))
        if match is None or not isinstance(entries, list):
            continue
        version = f"{match.group(1)}.{match.group(2)}"
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isAvailable") is False:
                continue
            name = entry.get("name")
            udid = entry.get("udid")
            if not isinstance(name, str) or not name.startswith("iPhone"):
                continue
            if not isinstance(udid, str) or (requested and udid != requested):
                continue
            candidates.append((entry.get("state") == "Booted", version, entry))
    return candidates


def _context_bundle(
    task: MarketingTask,
    request_id: str,
    device: DeviceTarget,
) -> MarketingContextBundle:
    country = _required_text(task.payload, "country", 2).upper()
    if country not in _LOCALES:
        raise MarketingExecutionError("native_capture_country_unsupported")
    image_inputs = task.payload.get("image_inputs")
    if not isinstance(image_inputs, dict):
        raise MarketingExecutionError("native_capture_image_inputs_invalid")
    try:
        trace_items = _TRACE_ITEMS.validate_python(image_inputs.get("trace_items"))
    except ValidationError as error:
        raise MarketingExecutionError("native_capture_trace_items_invalid") from error
    if not 1 <= len(trace_items) <= _MAX_TRACE_ITEMS:
        raise MarketingExecutionError("native_capture_trace_items_invalid")
    try:
        trace_todos = _TRACE_TODOS.validate_python(image_inputs.get("trace_todos", ()))
    except ValidationError as error:
        raise MarketingExecutionError("native_capture_trace_todos_invalid") from error
    if len(trace_todos) > _MAX_TRACE_TODOS or any(
        not todo.strip() or len(todo.strip()) > _MAX_TRACE_TODO_LENGTH for todo in trace_todos
    ):
        raise MarketingExecutionError("native_capture_trace_todos_invalid")
    profile = task.payload.get("context_profile")
    selected_profile = profile if isinstance(profile, dict) else {}
    persona_id = selected_profile.get("persona_id")
    if not isinstance(persona_id, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}",
        persona_id,
    ):
        persona_id = f"trace-{country.lower()}-public"
    reference_date = datetime.now(UTC)
    device_time = image_inputs.get("device_time")
    if isinstance(device_time, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", device_time):
        hour, minute = map(int, device_time.split(":"))
        reference_date = reference_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    profile_name = _profile_text(selected_profile, "name", persona_id, 80)
    tone = _profile_text(selected_profile, "tone", "natural", 300)
    audience = _profile_text(selected_profile, "audience", "Trace user", 500)
    situation = _profile_text(
        selected_profile,
        "situation",
        str(image_inputs.get("background_mood") or "daily life"),
        500,
    )
    topic = _required_text(task.payload, "topic", 200)
    caption = _required_text(task.payload, "caption", 10_000)
    hypothesis = _required_text(task.payload, "hypothesis", 2_000)
    creative_direction = _required_text(task.payload, "creative_direction", 20_000)
    background_intent = _background_query(task.payload, image_inputs)
    reference_ids = _reference_ids(task.payload.get("reference_ids"))
    language = _required_text(image_inputs, "language", 8)
    if language != _LOCALES[country].split("-", maxsplit=1)[0]:
        raise MarketingExecutionError("native_capture_language_mismatch")
    feedback_context, feedback_digest = _feedback_context(task)
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=request_id,
        campaign_id=task.task_id,
        persona=PersonaProfile(
            persona_id=persona_id,
            display_name=profile_name,
            country=country,
            locale=_LOCALES[country],
            age_group="adult",
            occupation=audience,
            traits=(tone,),
            interests=(situation,),
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id=f"hosted-{country.lower()}-trace",
            feature="lock_screen_schedule",
            concept=topic,
            tone=(tone,),
            caption=caption,
            hypothesis=hypothesis,
            reference_ids=reference_ids,
            creative_direction=creative_direction,
            background_intent=background_intent,
            trace_items=trace_items,
            trace_todos=tuple(todo.strip() for todo in trace_todos),
        ),
        feedback_context=feedback_context,
        feedback_context_sha256=feedback_digest,
        reference_date=reference_date,
        device=device,
    )


def _feedback_context(task: MarketingTask) -> tuple[FeedbackContext | None, str | None]:
    raw = task.payload.get("feedback_context")
    digest = task.payload.get("feedback_context_sha256")
    if raw is None and digest is None:
        return None, None
    if not isinstance(digest, str):
        raise MarketingExecutionError("native_capture_feedback_context_invalid")
    try:
        context = FeedbackContext.model_validate(raw)
    except ValidationError as error:
        raise MarketingExecutionError("native_capture_feedback_context_invalid") from error
    profile = task.payload.get("context_profile")
    profile_id = profile.get("profile_id") if isinstance(profile, dict) else None
    correction = context.immediate_correction
    candidate_id = _required_text(task.payload, "candidate_id", 80)
    candidate_revision = _required_integer(task.payload, "candidate_revision")
    if (
        context.stage != "image"
        or context.scope.account_id != task.account_id
        or context.scope.context_profile_id != profile_id
        or (
            correction is not None
            and (
                correction.source_candidate_id != candidate_id
                or correction.source_candidate_revision >= candidate_revision
            )
        )
        or feedback_context_sha256(context) != digest
    ):
        raise MarketingExecutionError("native_capture_feedback_context_invalid")
    return context, digest


def _profile_text(profile: JsonObject, key: str, default: str, max_length: int) -> str:
    value = profile.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise MarketingExecutionError("native_capture_context_profile_invalid")
    return value.strip()


def _task_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", value):
        raise MarketingExecutionError("native_capture_task_id_invalid")
    return value


def _reference_ids(value: JsonValue) -> tuple[str, ...]:
    try:
        supplied = _REFERENCE_IDS.validate_python(value)
    except ValidationError as error:
        raise MarketingExecutionError("native_capture_reference_ids_invalid") from error
    if len(supplied) > _MAX_REFERENCE_IDS:
        raise MarketingExecutionError("native_capture_reference_ids_invalid")
    identifiers: list[str] = []
    for item in supplied:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", item):
            raise MarketingExecutionError("native_capture_reference_ids_invalid")
        if item in identifiers:
            raise MarketingExecutionError("native_capture_reference_ids_invalid")
        identifiers.append(item)
    return tuple(identifiers)


def _background_query(payload: JsonObject, image_inputs: JsonObject) -> str:
    """The string the image stage runs as its background search.

    `background_search_query` is the one field generation is allowed to put a real
    character, person or team name in, and it is what the background search is meant to
    run. The worker also sends `background_intent`, which is composed mechanically as
    "<subject>: <mood>" and carries no proper noun at all - searching it returns news
    photography and shopping listings for the mood phrase, with the persona's actual
    interest nowhere in the results. Prefer the authored query and keep the composed
    intent as the fallback for candidates generated before the field existed.

    The composed intent leads with the vocabulary token, so the fallback used to search
    "sports_team: 밤 경기 외야석 너머 환한 전광판" - an English identifier no image index has
    ever labelled a photo with, which drags the whole query toward source code and datasets.
    The token is dropped here rather than at composition, because `background_intent` is the
    field a human writer may fill in freely and the composed pair is what the rest of the
    job reads.
    """
    query = image_inputs.get("background_search_query")
    if isinstance(query, str) and query.strip() and len(query.strip()) <= _SEARCH_QUERY_MAX:
        return query.strip()
    return _without_subject_token(_required_text(payload, "background_intent", 500))


def _without_subject_token(intent: str) -> str:
    """Drop a leading `"<subject>: "` from a mechanically composed background intent."""
    token, separator, remainder = intent.partition(": ")
    if separator and token in _BACKGROUND_SUBJECT_TOKENS and remainder.strip():
        return remainder.strip()
    return intent


def _required_text(payload: JsonObject, key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise MarketingExecutionError(f"native_capture_{key}_invalid")
    return value.strip()


def _required_integer(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MarketingExecutionError(f"native_capture_{key}_invalid")
    return value


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise MarketingExecutionError("native_capture_timeout_invalid") from error
    if timeout <= 0:
        raise MarketingExecutionError("native_capture_timeout_invalid")
    return timeout
