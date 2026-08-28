# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from ads_booster.capture.app_group_collector import SimctlAppGroupComponentCollector
from ads_booster.capture.appium_capture import (
    AppiumComponentExportAdapter,
    AppiumWallpaperExportAdapter,
)
from ads_booster.capture.appium_codex import (
    CodexAppiumWallpaperExportAdapter,
    StructuredCodexJob,
)
from ads_booster.capture.appium_session import DefaultAppiumSessionFactory
from ads_booster.capture.physical_adapter import UnavailablePhysicalDeviceAdapter
from ads_booster.capture.readiness import CaptureReadiness, DefaultCaptureReadiness
from ads_booster.capture.simulator_photo import SimctlPhotoImporter
from ads_booster.capture.wallpaper_collection import SimctlAppGroupWallpaperCollector
from ads_booster.contracts.models import DeviceKind

if TYPE_CHECKING:
    from ads_booster.capture.worker import SceneCaptureAdapter


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


def build_wallpaper_capture_adapter(
    device_kind: DeviceKind,
    appium_server: str,
    readiness: CaptureReadiness | None = None,
    codex: StructuredCodexJob | None = None,
) -> (
    AppiumWallpaperExportAdapter
    | CodexAppiumWallpaperExportAdapter
    | UnavailablePhysicalDeviceAdapter
):
    match device_kind:
        case DeviceKind.SIMULATOR:
            selected_readiness = readiness or DefaultCaptureReadiness(appium_server=appium_server)
            if codex is not None:
                return CodexAppiumWallpaperExportAdapter(
                    codex=codex,
                    simulator=SimctlPhotoImporter(),
                    collector=SimctlAppGroupWallpaperCollector(),
                    appium_server=appium_server,
                    readiness=selected_readiness,
                )
            return AppiumWallpaperExportAdapter(
                session_factory=DefaultAppiumSessionFactory(server_url=appium_server),
                simulator=SimctlPhotoImporter(),
                collector=SimctlAppGroupWallpaperCollector(),
                readiness=selected_readiness,
            )
        case DeviceKind.PHYSICAL:
            return UnavailablePhysicalDeviceAdapter()
        case unreachable:
            assert_never(unreachable)
