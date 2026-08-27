from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx2

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.contracts import ErrorCode
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.transport.http import create_http_client

TRACE_BUNDLE_ID = "com.corca.Trace"
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300


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
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"required runtime command is unavailable: {command[0]}",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message=f"runtime command exceeded the capture deadline: {command[0]}",
            ) from error
        return CommandResult(stdout=completed.stdout, returncode=completed.returncode)


class CaptureReadiness(Protocol):
    def ensure(self, device: DeviceTarget, control: CaptureControl) -> None: ...


@dataclass(frozen=True, slots=True)
class DefaultCaptureReadiness:
    appium_server: str
    command_runner: CommandRunner = SubprocessCommandRunner()
    appium_binary: str | None = None
    poll_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        """Validate the local Appium boundary before runtime startup."""
        _ = validate_appium_server_url(self.appium_server)

    def ensure(self, device: DeviceTarget, control: CaptureControl) -> None:
        match device.kind:
            case DeviceKind.SIMULATOR:
                self._ensure_simulator(device, control)
                self._ensure_appium(control)
            case DeviceKind.PHYSICAL:
                return

    def _ensure_simulator(self, device: DeviceTarget, control: CaptureControl) -> None:
        _ = self._run(("open", "-a", "Simulator"), control)
        booted = self._run(
            ("xcrun", "simctl", "list", "devices", "booted", "--json"),
            control,
        )
        if device.udid not in booted.stdout:
            boot = self._run(("xcrun", "simctl", "boot", device.udid), control)
            if boot.returncode != 0 and not self._is_booted(device, control):
                raise CaptureAdapterError(
                    code=ErrorCode.SCENE_CAPTURE_FAILED,
                    message=f"Simulator could not boot: {device.udid}",
                )
        _ = self._run(("xcrun", "simctl", "bootstatus", device.udid, "-b"), control)
        installed = self._run(("xcrun", "simctl", "listapps", device.udid), control)
        if TRACE_BUNDLE_ID not in installed.stdout:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=(
                    f"Trace Debug build is not installed on Simulator {device.udid}; "
                    f"expected {TRACE_BUNDLE_ID}"
                ),
            )

    def _is_booted(self, device: DeviceTarget, control: CaptureControl) -> bool:
        result = self._run(
            ("xcrun", "simctl", "list", "devices", "booted", "--json"),
            control,
        )
        return device.udid in result.stdout

    def _ensure_appium(self, control: CaptureControl) -> None:
        if self._appium_ready():
            return
        binary = self.appium_binary or shutil.which("appium")
        if binary is None:
            raise CaptureAdapterError(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                message="Appium is not installed or is not available on PATH",
            )
        try:
            _ = subprocess.Popen(  # noqa: S603
                [binary, "--port", str(_appium_port(self.appium_server))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                message="Appium could not be started",
            ) from error
        while not self._appium_ready():
            try:
                control.wait(self.poll_interval_seconds)
            except CaptureAdapterError as error:
                match error.code:
                    case ErrorCode.CAPTURE_TIMED_OUT:
                        raise CaptureAdapterError(
                            code=ErrorCode.APPIUM_UNAVAILABLE,
                            message="Appium did not become ready before the capture deadline",
                        ) from error
                    case _:
                        raise

    def _appium_ready(self) -> bool:
        try:
            with create_http_client() as http:
                response = http.get(f"{self.appium_server.rstrip('/')}/status", {})
        except httpx2.HTTPError:
            return False
        return HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES

    def _run(self, command: tuple[str, ...], control: CaptureControl) -> CommandResult:
        return self.command_runner.run(command, control.remaining_seconds())


def _appium_port(server_url: str) -> int:
    port = urlsplit(server_url).port
    if port is None:
        raise CaptureAdapterError(
            code=ErrorCode.APPIUM_ENDPOINT_REJECTED,
            message="Appium server URL must include a port",
        )
    return port
