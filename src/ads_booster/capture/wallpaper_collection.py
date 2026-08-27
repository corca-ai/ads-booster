from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ads_booster.capture.app_group_collector import (
    CommandRunner,
    SubprocessCommandRunner,
    parse_app_group_container,
)
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureClock,
    CaptureControl,
    SystemCaptureClock,
)
from ads_booster.capture.wallpaper_validation import (
    WallpaperExportBinding,
    file_signature,
    raise_if_wallpaper_export_failure,
    read_wallpaper_export_manifest,
    reject_symlink_path,
    validate_wallpaper_png,
)
from ads_booster.contracts import CaptureProvenance, ErrorCode, WallpaperExportManifest

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "SimctlAppGroupWallpaperCollector",
    "WallpaperCollectionRequest",
    "WallpaperExportBinding",
]


@dataclass(frozen=True, slots=True)
class WallpaperCollectionRequest:
    udid: str
    destination: Path
    binding: WallpaperExportBinding
    control: CaptureControl


@dataclass(frozen=True, slots=True)
class SimctlAppGroupWallpaperCollector:
    xcrun: str = "xcrun"
    bundle_id: str = "com.corca.Trace"
    group_id: str = "group.ai.corca.trace"
    filename: str = "trace_wallpaper.png"
    manifest_filename: str = "trace_wallpaper.manifest.json"
    failure_filename: str = "trace_wallpaper.error.json"
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    clock: CaptureClock = field(default_factory=SystemCaptureClock)
    poll_interval_seconds: float = 0.05
    manifest_grace_seconds: float = 1.0

    def clear(self, udid: str, control: CaptureControl) -> int:
        source, manifest, failure = self._export_paths(udid, control)
        try:
            source.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            failure.unlink(missing_ok=True)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace wallpaper export could not be cleared",
            ) from error
        cleared_at_ns = self.clock.time_ns()
        control.checkpoint()
        return cleared_at_ns

    def collect(self, request: WallpaperCollectionRequest) -> CaptureProvenance:
        if request.udid != request.binding.device_udid:
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_INVALID,
                message="wallpaper collection device does not match its export binding",
            )
        source, manifest_path, failure_path = self._export_paths(request.udid, request.control)
        source_modified_at_ns, manifest = self._wait_for_export(
            source,
            manifest_path,
            failure_path,
            request,
        )
        try:
            reject_symlink_path(request.destination.parent)
            request.destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_atomically(source, request.destination)
            manifest_destination = request.destination.with_suffix(".manifest.json")
            reject_symlink_path(manifest_destination)
            self._copy_atomically(manifest_path, manifest_destination)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace wallpaper export could not be copied",
            ) from error
        request.control.checkpoint()
        return validate_wallpaper_png(
            request.destination,
            request.binding,
            source_modified_at_ns,
            manifest,
        )

    def _wait_for_export(
        self,
        source: Path,
        manifest_path: Path,
        failure_path: Path,
        request: WallpaperCollectionRequest,
    ) -> tuple[int, WallpaperExportManifest]:
        previous_signature: tuple[int, int, int, int] | None = None
        source_seen_at: float | None = None
        while True:
            request.control.checkpoint()
            reject_symlink_path(source)
            reject_symlink_path(manifest_path)
            reject_symlink_path(failure_path)
            raise_if_wallpaper_export_failure(failure_path)
            if source.is_file():
                source_seen_at = source_seen_at or request.control.clock.monotonic()
                source_size, source_modified_at_ns = file_signature(source)
                if source_modified_at_ns < request.binding.cleared_at_ns:
                    raise CaptureAdapterError(
                        code=ErrorCode.EXPORT_STALE,
                        message="Trace wallpaper export predates the current Appium session",
                    )
                if manifest_path.is_file():
                    manifest_size, manifest_modified_at_ns = file_signature(manifest_path)
                    if manifest_modified_at_ns < request.binding.cleared_at_ns:
                        raise CaptureAdapterError(
                            code=ErrorCode.EXPORT_STALE,
                            message="Trace wallpaper manifest predates the current Appium session",
                        )
                    signature = (
                        source_size,
                        source_modified_at_ns,
                        manifest_size,
                        manifest_modified_at_ns,
                    )
                    if signature == previous_signature:
                        return source_modified_at_ns, read_wallpaper_export_manifest(manifest_path)
                    previous_signature = signature
                elif (
                    request.control.clock.monotonic() - source_seen_at
                    >= self.manifest_grace_seconds
                ):
                    raise CaptureAdapterError(
                        code=ErrorCode.EXPORT_UNVERIFIED,
                        message="Trace wallpaper export has no native binding manifest",
                    )
            request.control.wait(self.poll_interval_seconds)

    def _copy_atomically(self, source: Path, destination: Path) -> None:
        reject_symlink_path(source)
        reject_symlink_path(destination)
        temporary = destination.with_name(f".{destination.name}.tmp")
        reject_symlink_path(temporary)
        try:
            _ = shutil.copyfile(source, temporary)
            _ = temporary.replace(destination)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace wallpaper export could not be copied",
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    def _export_paths(self, udid: str, control: CaptureControl) -> tuple[Path, Path, Path]:
        completed = self.runner.run(
            (self.xcrun, "simctl", "get_app_container", udid, self.bundle_id, "groups"),
            control.remaining_seconds(),
        )
        if completed.returncode != 0:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"App Group lookup failed with exit code {completed.returncode}",
            )
        control.checkpoint()
        container = parse_app_group_container(completed.stdout, self.group_id)
        source = container / self.filename
        manifest = container / self.manifest_filename
        failure = container / self.failure_filename
        reject_symlink_path(container)
        reject_symlink_path(source)
        reject_symlink_path(manifest)
        reject_symlink_path(failure)
        return source, manifest, failure
