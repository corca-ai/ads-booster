from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.contracts import CaptureProvenance, ErrorCode

if TYPE_CHECKING:
    from trace_capture.capture.worker import CaptureRequest


@dataclass(frozen=True, slots=True)
class UnavailablePhysicalDeviceAdapter:
    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        raise CaptureAdapterError(
            code=ErrorCode.PHYSICAL_DEVICE_UNAVAILABLE,
            message=(
                "physical iPhone capture requires a connected device and a verified "
                f"wallpaper path: {request.device.udid}"
            ),
        )
