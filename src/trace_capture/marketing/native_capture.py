from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from trace_capture.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from trace_capture.contracts.models import DeviceKind, DeviceTarget
from trace_capture.contracts.run import TraceRunState
from trace_capture.marketing.inbox import MarketingExecutionError
from trace_capture.marketing.models import MarketingTask, TaskResult, TaskStatus
from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.automation import GenerateOnePort
    from trace_capture.marketing.bridge import TaskExecutor

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
_MAX_TRACE_ITEM_LENGTH: Final = 80


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
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise MarketingExecutionError("native_simulator_inventory_invalid") from error
        devices = payload.get("devices")
        if not isinstance(devices, dict):
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
        request_id = f"hosted-{task.task_id}"[:80]
        bundle = _context_bundle(task, request_id, self.device_resolver.resolve())
        result = self.runner.run(bundle)
        if result.state is not TraceRunState.COMPLETED or result.output_image is None:
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
    raw_items = image_inputs.get("trace_items")
    if (
        not isinstance(raw_items, list)
        or not 1 <= len(raw_items) <= _MAX_TRACE_ITEMS
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item.strip()) > _MAX_TRACE_ITEM_LENGTH
            for item in raw_items
        )
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
    tone = str(selected_profile.get("tone") or "natural")[:80]
    audience = str(selected_profile.get("audience") or "Trace user")[:80]
    situation = str(
        selected_profile.get("situation")
        or image_inputs.get("background_mood")
        or "daily life"
    )[:80]
    topic = _required_text(task.payload, "topic", 200)
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=request_id,
        persona=PersonaProfile(
            persona_id=persona_id,
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
            concept=topic[:120],
            tone=(tone,),
            trace_items=tuple(item.strip() for item in raw_items),
        ),
        reference_date=reference_date,
        device=device,
    )


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
