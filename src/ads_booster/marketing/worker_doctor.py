from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ads_booster.marketing.models import TaskKind
from ads_booster.providers.codex_cli import resolve_codex_executable
from ads_booster.transport.json_types import JsonObject

_PACKAGE_NAME: Final = "trace-appium-capture"
# The job kinds this build of the worker can run. `TaskKind` is the full vocabulary the
# control plane knows; this is the subset a Mac worker actually executes today.
TASK_KINDS: Final = (TaskKind.CAPTURE.value, TaskKind.GENERATE_CANDIDATES.value)
_TRACE_BUNDLE_ID: Final = "com.corca.Trace"
_RUNTIME_PART_COUNT: Final = 2
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class MacWorkerDoctorReport:
    ready: bool
    summary: str
    checks: dict[str, bool]
    version: str

    def heartbeat(self) -> JsonObject:
        return {
            "version": self.version,
            "capabilities": {
                "native_appium": True,
                "hosted_workspace_capture_v1": True,
                # Which job kinds this worker can actually execute, so the control plane does
                # not lease a caption batch to a Mac whose Python predates it. A comma-joined
                # string rather than a list because the control plane flattens every non-scalar
                # capability value to null; a worker that says nothing is read as capture-only,
                # which is what a worker from before this field could do.
                "task_kinds": ",".join(TASK_KINDS),
            },
            "doctor": {"ready": self.ready, "summary": self.summary},
        }


def inspect_mac_worker(
    *,
    codex_executable: Path | None = None,
    resolve_codex: bool = True,
) -> MacWorkerDoctorReport:
    is_macos = platform.system() == "Darwin"
    codex = resolve_codex_executable() if resolve_codex else codex_executable
    codex_available = codex is not None and codex.is_file() and os.access(codex, os.X_OK)
    codex_authenticated = (
        codex_available and codex is not None and _run((str(codex), "login", "status")) is not None
    )
    xcrun = shutil.which("xcrun")
    appium = shutil.which("appium")
    simulator_available = False
    trace_installed = False
    xcuitest_installed = False
    if is_macos and xcrun:
        simulator_available, trace_installed = _simulator_checks(xcrun)
    if appium:
        xcuitest_installed = _xcuitest_check(appium)
    checks = {
        "macos": is_macos,
        "xcrun": xcrun is not None,
        "appium": appium is not None,
        "xcuitest_driver": xcuitest_installed,
        "available_iphone_simulator": simulator_available,
        "trace_debug_build": trace_installed,
        "codex_cli": codex_available,
        "codex_authenticated": codex_authenticated,
    }
    missing = [name for name, passed in checks.items() if not passed]
    ready = not missing
    summary = "ready" if ready else f"missing: {', '.join(missing)}"
    return MacWorkerDoctorReport(
        ready=ready,
        summary=summary,
        checks=checks,
        version=installed_version(),
    )


def _simulator_checks(xcrun: str) -> tuple[bool, bool]:
    inventory = _run((xcrun, "simctl", "list", "devices", "available", "--json"))
    if inventory is None:
        return False, False
    try:
        payload = _JSON_OBJECT.validate_json(inventory)
    except ValidationError:
        return False, False
    devices = payload.get("devices")
    if not isinstance(devices, dict):
        return False, False
    simulators = _available_simulators(devices)
    if not simulators:
        return False, False
    booted, _version, simulator_id = max(
        simulators,
        key=lambda value: (value[0], value[1]),
    )
    # listapps is not reliable for a shutdown Simulator. The worker doctor owns
    # readiness, so it prepares the selected runtime before advertising ready.
    if not booted:
        _ = _run((xcrun, "simctl", "boot", simulator_id))
    if _run((xcrun, "simctl", "bootstatus", simulator_id, "-b")) is None:
        return True, False
    applications = _run((xcrun, "simctl", "listapps", simulator_id))
    return True, applications is not None and _TRACE_BUNDLE_ID in applications


def _available_simulators(devices: JsonObject) -> list[tuple[bool, tuple[int, int], str]]:
    simulators: list[tuple[bool, tuple[int, int], str]] = []
    for runtime, entries in devices.items():
        if not isinstance(entries, list):
            continue
        version = _runtime_version(str(runtime))
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isAvailable") is False:
                continue
            name = entry.get("name")
            simulator_id = entry.get("udid")
            if (
                isinstance(name, str)
                and name.startswith("iPhone")
                and isinstance(simulator_id, str)
            ):
                simulators.append((entry.get("state") == "Booted", version, simulator_id))
    return simulators


def _runtime_version(runtime: str) -> tuple[int, int]:
    pieces = runtime.rsplit("iOS-", maxsplit=1)
    if len(pieces) != _RUNTIME_PART_COUNT:
        return (0, 0)
    numbers = pieces[1].split("-")
    try:
        return (int(numbers[0]), int(numbers[1]))
    except IndexError, ValueError:
        return (0, 0)


def _xcuitest_check(appium: str) -> bool:
    output = _run((appium, "driver", "list", "--installed", "--json"))
    if output is None:
        return False
    try:
        payload = _JSON_OBJECT.validate_json(output)
    except ValidationError:
        return "xcuitest" in output.lower()
    return any("xcuitest" in key.lower() for key in payload)


def _run(command: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.stdout if result.returncode == 0 else None


def installed_version() -> str:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return "source"
