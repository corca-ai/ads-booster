from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.contracts import CaptureProvenance, ErrorCode

if TYPE_CHECKING:
    from ads_booster.capture.worker import CaptureRequest
    from ads_booster.contracts import WallpaperPlan


@dataclass(frozen=True, slots=True)
class UnavailablePhysicalDeviceAdapter:
    def capture(
        self,
        request: CaptureRequest,
        plan: WallpaperPlan | None = None,
    ) -> CaptureProvenance:
        del plan
        raise CaptureAdapterError(
            code=ErrorCode.PHYSICAL_DEVICE_UNAVAILABLE,
            message=(
                "physical iPhone capture requires a connected device and a verified "
                f"wallpaper path: {request.device.udid}"
            ),
        )
