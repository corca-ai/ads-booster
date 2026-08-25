from __future__ import annotations

import base64
import json
from datetime import UTC
from hashlib import sha256
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from trace_capture.capture.worker import CaptureRequest


class ProcessArguments(TypedDict):
    args: list[str]
    env: dict[str, str]


def build_process_arguments(request: CaptureRequest) -> ProcessArguments:
    items_json = json.dumps(
        request.scene.trace_data.items,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    encoded_items = base64.b64encode(items_json).decode("ascii")
    request_digest = capture_request_digest(request)
    reference_date = (
        request.scene.reference_date.astimezone(UTC)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
    return ProcessArguments(
        args=[
            "-traceMarketingFixture",
            "-traceMarketingFixtureItems",
            encoded_items,
            "-traceMarketingReferenceDate",
            reference_date,
            "-traceMarketingSurface",
            "calendar",
            "-traceMarketingExportComponents",
            "-traceMarketingRequestDigest",
            request_digest,
            "-traceMarketingExportNonce",
            request.capture_nonce,
            "-traceMarketingDeviceUDID",
            request.device.udid,
        ],
        env={},
    )


def capture_request_digest(request: CaptureRequest) -> str:
    canonical = (
        f"{request.job_id}\n{request.device.model_dump_json()}\n{request.scene.model_dump_json()}"
    )
    return sha256(canonical.encode()).hexdigest()
