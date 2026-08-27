from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ads_booster.capture.appium_process import capture_request_digest
from ads_booster.capture.appium_session import TRACE_BUNDLE_ID, AppiumSessionFactory
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    CaptureLeaseFactory,
    ComponentCollectionRequest,
    ExportBinding,
    UdidCaptureLeaseFactory,
)
from ads_booster.capture.wallpaper_collection import (
    WallpaperCollectionRequest,
    WallpaperExportBinding,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.readiness import CaptureReadiness
    from ads_booster.capture.worker import CaptureRequest
    from ads_booster.contracts import WallpaperPlan

    from .appium_session import AppiumSession


class SimulatorController(Protocol):
    def supports_custom_photo_wallpaper(self) -> bool: ...
    def import_background(self, udid: str, background: Path) -> None: ...
    def capture_screen(self, udid: str, destination: Path) -> None: ...


class AppGroupComponentCollector(Protocol):
    def clear(self, udid: str, control: CaptureControl) -> int: ...
    def collect(self, request: ComponentCollectionRequest) -> CaptureProvenance: ...


class SimulatorPhotoImporter(Protocol):
    def import_background(
        self,
        udid: str,
        background: Path,
        control: CaptureControl,
    ) -> None: ...


class AppGroupWallpaperCollector(Protocol):
    def clear(self, udid: str, control: CaptureControl) -> int: ...
    def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance: ...


@dataclass(frozen=True, slots=True)
class AppiumCaptureAdapter:
    session_factory: AppiumSessionFactory
    simulator: SimulatorController
    readiness: CaptureReadiness | None = None

    def capture(self, request: CaptureRequest) -> None:
        if request.background is None:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="full-screen capture requires a background image",
            )
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        self.simulator.import_background(request.device.udid, request.background)
        session = self.session_factory.open(request)
        try:
            session.lock(0, request.control)
            try:
                if not session.is_locked(request.control):
                    raise CaptureAdapterError(
                        code=ErrorCode.LOCK_SCREEN_UNAVAILABLE,
                        message="Appium returned without locking the simulator",
                    )
                self.simulator.capture_screen(request.device.udid, request.destination)
                if not self.simulator.supports_custom_photo_wallpaper():
                    raise CaptureAdapterError(
                        code=ErrorCode.LOCK_SCREEN_UNAVAILABLE,
                        message=(
                            "iOS Simulator captured a diagnostic frame but cannot "
                            "render custom photo wallpaper"
                        ),
                    )
            finally:
                session.unlock(request.control)
        finally:
            session.quit(request.control)


@dataclass(frozen=True, slots=True)
class AppiumWallpaperExportAdapter:
    session_factory: AppiumSessionFactory
    simulator: SimulatorPhotoImporter
    collector: AppGroupWallpaperCollector
    lease_factory: CaptureLeaseFactory = field(default_factory=UdidCaptureLeaseFactory)
    readiness: CaptureReadiness | None = None
    cleanup_timeout_seconds: float = 300.0

    def capture(self, request: CaptureRequest, plan: WallpaperPlan) -> CaptureProvenance:
        if request.background is None:
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="full wallpaper export requires a searched background image",
            )
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        with self.lease_factory.acquire(request.device.udid):
            request.control.checkpoint()
            cleared_at_ns = self.collector.clear(request.device.udid, request.control)
            self.simulator.import_background(
                request.device.udid,
                request.background,
                request.control,
            )
            configuration_session = self.session_factory.open_configuration(request, plan)
            try:
                configuration_session.reset_application(request.control)
                configuration_session.configure_wallpaper(
                    plan,
                    select_background=True,
                    control=request.control,
                    reference_date=request.scene.reference_date,
                )
                binding = WallpaperExportBinding(
                    request_sha256=capture_request_digest(request, plan),
                    bundle_id=TRACE_BUNDLE_ID,
                    device_udid=request.device.udid,
                    session_id=configuration_session.session_id(request.control),
                    cleared_at_ns=cleared_at_ns,
                    export_nonce=request.capture_nonce,
                )
                provenance = self.collector.collect(
                    WallpaperCollectionRequest(
                        udid=request.device.udid,
                        destination=request.destination,
                        binding=binding,
                        control=request.control,
                    )
                )
            except CaptureAdapterError as primary_error:
                cleanup_error = self._cleanup_configuration(configuration_session, plan)
                if cleanup_error is not None:
                    cleanup_detail = cleanup_error.message
                    if cleanup_error.cleanup_error is not None:
                        cleanup_detail = (
                            f"{cleanup_detail}; session quit: {cleanup_error.cleanup_error}"
                        )
                    raise primary_error.with_cleanup_error(
                        cleanup_detail,
                    ) from primary_error
                raise
            else:
                cleanup_error = self._cleanup_configuration(configuration_session, plan)
                if cleanup_error is not None:
                    raise cleanup_error
                return provenance

    def _cleanup_configuration(
        self,
        session: AppiumSession,
        plan: WallpaperPlan,
    ) -> CaptureAdapterError | None:
        control = CaptureControl.start(self.cleanup_timeout_seconds)
        cleanup_error: CaptureAdapterError | None = None
        try:
            session.cleanup_wallpaper(plan, control)
        except CaptureAdapterError as error:
            cleanup_error = error
        try:
            session.quit(control)
        except CaptureAdapterError as error:
            if cleanup_error is None:
                return error
            return cleanup_error.with_cleanup_error(error.message)
        return cleanup_error


@dataclass(frozen=True, slots=True)
class AppiumComponentExportAdapter:
    session_factory: AppiumSessionFactory
    collector: AppGroupComponentCollector
    lease_factory: CaptureLeaseFactory = field(default_factory=UdidCaptureLeaseFactory)
    readiness: CaptureReadiness | None = None

    def capture(self, request: CaptureRequest) -> CaptureProvenance:
        if self.readiness is not None:
            self.readiness.ensure(request.device, request.control)
        with self.lease_factory.acquire(request.device.udid):
            request.control.checkpoint()
            cleared_at_ns = self.collector.clear(request.device.udid, request.control)
            request.control.checkpoint()
            configuration_session = self.session_factory.open_export(request)
            try:
                configuration_session.configure_components(request.control)
                if request.iphone_ui_destination is not None:
                    configuration_session.screenshot(
                        request.iphone_ui_destination,
                        request.control,
                    )
                binding = ExportBinding(
                    request_sha256=capture_request_digest(request),
                    bundle_id=TRACE_BUNDLE_ID,
                    device_udid=request.device.udid,
                    session_id=configuration_session.session_id(request.control),
                    cleared_at_ns=cleared_at_ns,
                    export_nonce=request.capture_nonce,
                    expected_width=(
                        request.scene.component_canvas.width
                        if request.scene.component_canvas is not None
                        else None
                    ),
                    expected_height=(
                        request.scene.component_canvas.height
                        if request.scene.component_canvas is not None
                        else None
                    ),
                )
                provenance = self.collector.collect(
                    ComponentCollectionRequest(
                        udid=request.device.udid,
                        destination=request.destination,
                        binding=binding,
                        control=request.control,
                    ),
                )
            except CaptureAdapterError as primary_error:
                try:
                    configuration_session.quit(request.control)
                except CaptureAdapterError as cleanup_error:
                    raise primary_error.with_cleanup_error(
                        cleanup_error.message,
                    ) from primary_error
                raise
            else:
                configuration_session.quit(request.control)
                return provenance
