from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from trace_capture.capture.worker import CaptureRequest


class ProcessArguments(TypedDict):
    args: list[str]
    env: dict[str, str]


def build_configuration_process_arguments() -> ProcessArguments:
    return ProcessArguments(args=["-traceMarketingAutomation"], env={})


def build_process_arguments(request: CaptureRequest) -> ProcessArguments:
    request_digest = capture_request_digest(request)
    return ProcessArguments(
        args=[
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
