from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.contracts.run import TraceRunErrorCode, TraceRunState
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskResult, TaskStatus
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.automation import GenerateOnePort
    from ads_booster.marketing.bridge import TaskExecutor

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
_RUNTIME_VERSION = re.compile(r"\.iOS-(\d+)-(\d+)$")
_MAX_TRACE_ITEMS: Final = 8
_MAX_REFERENCE_IDS: Final = 16
_MAX_TRACE_ITEM_LENGTH: Final = 80
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_TRACE_ITEMS: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])
_REFERENCE_IDS: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[str, ...])


class DeviceResolver(Protocol):
    def resolve(self) -> DeviceTarget: ...


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
class HostedWorkspaceCaptureExecutor:
    runner: GenerateOnePort
    output_root: Path
    device_resolver: DeviceResolver = SimctlDeviceResolver()

    def execute(self, task: MarketingTask) -> TaskResult:
        if task.payload.get("pipeline") != _PIPELINE:
            raise MarketingExecutionError("unsupported_hosted_capture_pipeline")
        request_id = _task_identifier(task.task_id)
        bundle = _context_bundle(task, request_id, self.device_resolver.resolve())
        result = self.runner.run(bundle)
        if result.state is TraceRunState.UNKNOWN_SIDE_EFFECT:
            raise MarketingExecutionError(
                "native_appium_side_effect_unknown",
                unknown_side_effect=True,
            )
        if result.state is not TraceRunState.COMPLETED or result.output_image is None:
            if (
                result.failure is not None
                and result.failure.code is TraceRunErrorCode.CODEX_PLAN_FAILED
            ):
                raise MarketingExecutionError("codex_plan_failed")
            raise MarketingExecutionError("native_appium_capture_failed")
        provenance = result.capture_provenance
        if (
            provenance is None
            or provenance.source != "native_appium"
            or not provenance.native_export_binding_verified
        ):
            raise MarketingExecutionError("native_capture_provenance_unverified")
        image_path = (self.output_root / request_id / result.output_image).resolve()
        root = self.output_root.resolve()
        if not image_path.is_relative_to(root) or not image_path.is_file():
            raise MarketingExecutionError("native_capture_artifact_missing")
        image = image_path.read_bytes()
        if not image or len(image) > _MAX_IMAGE_BYTES:
            raise MarketingExecutionError("native_capture_artifact_size_invalid")
        digest = sha256(image).hexdigest()
        if result.output_image_sha256 != digest:
            raise MarketingExecutionError("native_capture_artifact_digest_mismatch")
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": _PIPELINE,
                "candidate_id": _required_text(task.payload, "candidate_id", 128),
                "candidate_revision": _required_integer(task.payload, "candidate_revision"),
                "content_type": "image/png",
                "image_sha256": digest,
                "image_base64": base64.b64encode(image).decode("ascii"),
                "capture_source": "native_appium",
                "native_export_binding_verified": True,
            },
        )


@dataclass(frozen=True, slots=True)
class HostedCaptureRoutingExecutor:
    hosted: HostedWorkspaceCaptureExecutor
    fallback: TaskExecutor

    def execute(self, task: MarketingTask) -> TaskResult:
        if task.payload.get("pipeline") == _PIPELINE:
            return self.hosted.execute(task)
        return self.fallback.execute(task)


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
    if not 1 <= len(trace_items) <= _MAX_TRACE_ITEMS or any(
        not item.strip() or len(item.strip()) > _MAX_TRACE_ITEM_LENGTH for item in trace_items
    ):
        raise MarketingExecutionError("native_capture_trace_items_invalid")
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
    background_intent = _required_text(task.payload, "background_intent", 500)
    reference_ids = _reference_ids(task.payload.get("reference_ids"))
    language = _required_text(image_inputs, "language", 8)
    if language != _LOCALES[country].split("-", maxsplit=1)[0]:
        raise MarketingExecutionError("native_capture_language_mismatch")
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
            trace_items=tuple(item.strip() for item in trace_items),
        ),
        reference_date=reference_date,
        device=device,
    )


def _profile_text(profile: JsonObject, key: str, default: str, max_length: int) -> str:
    value = profile.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise MarketingExecutionError("native_capture_context_profile_invalid")
    return value.strip()


def _task_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", value):
        raise MarketingExecutionError("native_capture_task_id_invalid")
    return value


def _reference_ids(value: object) -> tuple[str, ...]:
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
