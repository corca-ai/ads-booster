from __future__ import annotations

import base64
import json
from hashlib import sha256
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ads_booster.capture.worker import CaptureRequest
    from ads_booster.contracts import WallpaperPlan


class ProcessArguments(TypedDict):
    args: list[str]
    env: dict[str, str]


def build_configuration_process_arguments(
    request: CaptureRequest,
    request_sha256: str,
) -> ProcessArguments:
    return build_process_arguments(request, request_sha256)


def build_process_arguments(
    request: CaptureRequest,
    request_sha256: str,
) -> ProcessArguments:
    return ProcessArguments(
        args=[
            "-traceMarketingAutomation",
            "-traceMarketingExportWallpaper",
            "-traceMarketingRequestDigest",
            request_sha256,
            "-traceMarketingExportNonce",
            request.capture_nonce,
            "-traceMarketingDeviceUDID",
            request.device.udid,
        ],
        env={},
    )


def build_component_process_arguments(request: CaptureRequest) -> ProcessArguments:
    encoded_components = base64.b64encode(
        json.dumps(
            request.scene.trace_data.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
    ).decode()
    return ProcessArguments(
        args=[
            "-traceMarketingAutomation",
            "-traceMarketingComponentPayload",
            encoded_components,
            "-traceMarketingExportComponents",
            "-traceMarketingRequestDigest",
            capture_request_digest(request),
            "-traceMarketingExportNonce",
            request.capture_nonce,
            "-traceMarketingDeviceUDID",
            request.device.udid,
        ],
        env={},
    )


def capture_request_digest(
    request: CaptureRequest,
    plan: WallpaperPlan | None = None,
) -> str:
    canonical = (
        f"{request.job_id}\n{request.device.model_dump_json()}\n{request.scene.model_dump_json()}"
    )
    if plan is not None:
        canonical = f"{canonical}\n{plan.model_dump_json()}"
    return sha256(canonical.encode()).hexdigest()
