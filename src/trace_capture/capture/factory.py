# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from trace_capture.capture.app_group_collector import SimctlAppGroupComponentCollector
from trace_capture.capture.appium_adapter import (
    AppiumComponentExportAdapter,
    DefaultAppiumSessionFactory,
)
from trace_capture.capture.physical_adapter import UnavailablePhysicalDeviceAdapter
from trace_capture.capture.readiness import CaptureReadiness, DefaultCaptureReadiness
from trace_capture.contracts.models import DeviceKind

if TYPE_CHECKING:
    from trace_capture.capture.worker import SceneCaptureAdapter


def build_capture_adapter(
    device_kind: DeviceKind,
    appium_server: str,
    readiness: CaptureReadiness | None = None,
) -> SceneCaptureAdapter:
    match device_kind:
        case DeviceKind.SIMULATOR:
            selected_readiness = readiness or DefaultCaptureReadiness(appium_server=appium_server)
            return AppiumComponentExportAdapter(
                session_factory=DefaultAppiumSessionFactory(server_url=appium_server),
                collector=SimctlAppGroupComponentCollector(),
                readiness=selected_readiness,
            )
        case DeviceKind.PHYSICAL:
            return UnavailablePhysicalDeviceAdapter()
        case unreachable:
            assert_never(unreachable)
