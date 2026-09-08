"""Read-only Mac capture readiness: no device boot, app launch, permissions or daemon startup."""

from __future__ import annotations

import os
import platform
import shutil
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import TypeAdapter

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.simctl_command import CommandRunner, SubprocessCommandRunner
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_service.oauth import NoRedirect
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

_TIMEOUT = 3.0
_MAX_BYTES = 64 * 1024
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class StatusResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def geturl(self) -> str: ...
    def getcode(self) -> int: ...
    def close(self) -> None: ...


def _open(request: Request, *, timeout: float) -> StatusResponse:
    return cast(
        "StatusResponse",
        build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout),
    )


@dataclass(frozen=True, slots=True)
class CaptureReadinessProbe:
    device: DeviceTarget
    appium_server: str
    codex_executable: Path
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    opener: Callable[..., StatusResponse] = field(default=_open, repr=False)
    platform_name: str = field(default_factory=platform.system)
    lookup: Callable[[str], str | None] = field(default=shutil.which, repr=False)

    def check(self, now: datetime) -> ToolReadiness:
        reason = "capture_readiness_unavailable"
        try:
            reason = self._check()
        except Exception:  # noqa: BLE001 - external probes fail closed with no raw output/secrets.
            reason = "capture_readiness_unavailable"
        return ToolReadiness(
            ready=reason is None, reason_code=reason, observed_at=now, max_age_seconds=30
        )

    def _check(self) -> str | None:  # noqa: PLR0911 - distinct safe readiness reasons.
        endpoint = validate_appium_server_url(self.appium_server).rstrip("/") + "/status"
        if self.platform_name != "Darwin" or self.device.kind != DeviceKind.SIMULATOR:
            return "capture_mac_simulator_required"
        if not self.codex_executable.is_file() or not os.access(self.codex_executable, os.X_OK):
            return "capture_codex_unavailable"
        xcrun, appium = self.lookup("xcrun"), self.lookup("appium")
        if xcrun is None or appium is None:
            return "capture_commands_unavailable"
        deadline = time.monotonic() + _TIMEOUT
        inventory = _JSON.validate_json(
            self._command((xcrun, "simctl", "list", "devices", "booted", "--json"), deadline)
        )
        devices = inventory.get("devices")
        if not isinstance(devices, dict) or not any(
            isinstance(group, list)
            and any(
                isinstance(item, dict)
                and item.get("udid") == self.device.udid
                and item.get("state") == "Booted"
                and item.get("isAvailable") is True
                for item in group
            )
            for group in devices.values()
        ):
            return "capture_requested_simulator_not_booted"
        container = self._command(
            (xcrun, "simctl", "get_app_container", self.device.udid, "com.corca.Trace", "app"),
            deadline,
        ).strip()
        if not container or "\n" in container or "\r" in container:
            return "capture_trace_app_missing"
        app = Path(container)
        if not app.is_absolute() or not app.is_dir():
            return "capture_trace_app_missing"
        response = self.opener(
            Request(endpoint, method="GET"),  # noqa: S310 - validated literal loopback HTTP.
            timeout=self._remaining(deadline),
        )
        try:
            if response.geturl() != endpoint or response.getcode() != HTTPStatus.OK:
                return "capture_appium_status_unverified"
            data = response.read(_MAX_BYTES)
            if len(data) >= _MAX_BYTES:
                return "capture_appium_status_unverified"
            body = _JSON.validate_json(data)
            value = body.get("value")
            if not isinstance(value, dict) or value.get("ready") is not True:
                return "capture_appium_not_ready"
        finally:
            response.close()
        return None

    def _command(self, command: tuple[str, ...], deadline: float) -> str:
        result = self.runner.run(command, self._remaining(deadline))
        if result.returncode != 0 or len(result.stdout.encode()) > _MAX_BYTES:
            raise ValueError("capture_readiness_command_failed")
        return result.stdout

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("capture_readiness_timeout")
        return min(remaining, _TIMEOUT)
