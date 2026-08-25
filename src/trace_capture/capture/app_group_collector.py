from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from trace_capture.capture.app_group_validation import (
    raise_export_failure,
    read_component_export_manifest,
)
from trace_capture.capture.artifact_validation import validate_component_png
from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureClock,
    CaptureControl,
    ComponentCollectionRequest,
    SystemCaptureClock,
    path_has_symlink_component,
)
from trace_capture.contracts import (
    CaptureProvenance,
    ComponentExportManifest,
    ErrorCode,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    returncode: int


class CommandRunner(Protocol):
    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class SubprocessCommandRunner:
    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"simulator command is unavailable: {command[0]}",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message="simulator command exceeded the capture deadline",
            ) from error
        return CommandResult(stdout=completed.stdout, returncode=completed.returncode)


def parse_app_group_container(output: str, group_id: str) -> Path:
    for line in output.splitlines():
        reported_group, separator, reported_path = line.partition("\t")
        if separator and reported_group == group_id and reported_path:
            return Path(reported_path)
    raise CaptureAdapterError(
        code=ErrorCode.SCENE_CAPTURE_FAILED,
        message=f"Trace App Group container is unavailable: {group_id}",
    )


@dataclass(frozen=True, slots=True)
class SimctlAppGroupComponentCollector:
    xcrun: str = "xcrun"
    bundle_id: str = "com.corca.Trace"
    group_id: str = "group.ai.corca.trace"
    filename: str = "trace_components.png"
    manifest_filename: str = "trace_components.manifest.json"
    failure_filename: str = "trace_components.error.json"
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    clock: CaptureClock = field(default_factory=SystemCaptureClock)
    poll_interval_seconds: float = 0.05
    manifest_grace_seconds: float = 1.0

    def clear(self, udid: str, control: CaptureControl) -> int:
        export_path, manifest_path, failure_path = self._export_paths(udid, control)
        try:
            export_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            failure_path.unlink(missing_ok=True)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace component export could not be cleared",
            ) from error
        cleared_at_ns = self.clock.time_ns()
        control.checkpoint()
        return cleared_at_ns

    def collect(self, request: ComponentCollectionRequest) -> CaptureProvenance:
        source, manifest_path, failure_path = self._export_paths(
            request.udid,
            request.control,
        )
        source_modified_at_ns, manifest = self._wait_for_export(
            source,
            manifest_path,
            failure_path,
            request,
        )
        try:
            _reject_symlink_path(request.destination.parent)
            request.destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_atomically(source, request.destination)
            if manifest is not None:
                manifest_destination = request.destination.with_suffix(".manifest.json")
                _reject_symlink_path(manifest_destination)
                self._copy_atomically(manifest_path, manifest_destination)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace component export could not be copied",
            ) from error
        request.control.checkpoint()
        provenance = validate_component_png(
            request.destination,
            request.binding,
            source_modified_at_ns,
            manifest,
        )
        if manifest is None:
            raise CaptureAdapterError(
                code=ErrorCode.EXPORT_UNVERIFIED,
                message="Trace component export has no native binding manifest",
            )
        return provenance

    def _wait_for_export(
        self,
        source: Path,
        manifest_path: Path,
        failure_path: Path,
        request: ComponentCollectionRequest,
    ) -> tuple[int, ComponentExportManifest | None]:
        previous_signature: tuple[int, int, int, int] | None = None
        source_seen_at: float | None = None
        while True:
            request.control.checkpoint()
            _reject_symlink_path(source)
            _reject_symlink_path(manifest_path)
            _reject_symlink_path(failure_path)
            _raise_if_failure_marker(failure_path)
            if source.is_file():
                if source_seen_at is None:
                    source_seen_at = request.control.clock.monotonic()
                try:
                    source_stat = source.stat()
                except OSError as error:
                    raise CaptureAdapterError(
                        code=ErrorCode.SCENE_CAPTURE_FAILED,
                        message="Trace component export metadata could not be read",
                    ) from error
                if source_stat.st_mtime_ns < request.binding.cleared_at_ns:
                    raise CaptureAdapterError(
                        code=ErrorCode.EXPORT_STALE,
                        message="Trace component export predates the current Appium session",
                    )
                if not manifest_path.is_file():
                    if (
                        request.control.clock.monotonic() - source_seen_at
                        >= self.manifest_grace_seconds
                    ):
                        return source_stat.st_mtime_ns, None
                else:
                    try:
                        manifest_stat = manifest_path.stat()
                    except OSError as error:
                        raise CaptureAdapterError(
                            code=ErrorCode.SCENE_CAPTURE_FAILED,
                            message="Trace component export metadata could not be read",
                        ) from error
                    signature = (
                        source_stat.st_size,
                        source_stat.st_mtime_ns,
                        manifest_stat.st_size,
                        manifest_stat.st_mtime_ns,
                    )
                    if signature == previous_signature:
                        manifest = read_component_export_manifest(manifest_path)
                        return source_stat.st_mtime_ns, manifest
                    previous_signature = signature
            request.control.wait(self.poll_interval_seconds)

    def _copy_atomically(self, source: Path, destination: Path) -> None:
        _reject_symlink_path(source)
        _reject_symlink_path(destination)
        temporary = destination.with_name(f".{destination.name}.tmp")
        _reject_symlink_path(temporary)
        try:
            _ = shutil.copyfile(source, temporary)
            _ = temporary.replace(destination)
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Trace component export could not be copied",
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    def _export_paths(
        self,
        udid: str,
        control: CaptureControl,
    ) -> tuple[Path, Path, Path]:
        command = (
            self.xcrun,
            "simctl",
            "get_app_container",
            udid,
            self.bundle_id,
            "groups",
        )
        completed = self.runner.run(command, control.remaining_seconds())
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
        _reject_symlink_path(container)
        _reject_symlink_path(source)
        _reject_symlink_path(manifest)
        _reject_symlink_path(failure)
        return source, manifest, failure


def _reject_symlink_path(path: Path) -> None:
    if path_has_symlink_component(path):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace component export path contains a symlink",
        )


def _raise_if_failure_marker(path: Path) -> None:
    if path.is_file():
        raise_export_failure(path)
