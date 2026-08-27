from __future__ import annotations

import os
import plistlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

_WORKER_LABEL: Final = "com.corca.trace-marketing-worker"
_UPDATER_LABEL: Final = "com.corca.trace-marketing-updater"
_LAUNCHCTL: Final = "/bin/launchctl"
_DEFAULT_PATH: Final = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_WORKER_ENV_ALLOWLIST: Final = (
    "TRACE_CODEX_MODEL",
    "TRACE_CODEX_TIMEOUT_SECONDS",
    "TRACE_AGENT_APPIUM_SERVER",
    "TRACE_AGENT_GENERATION_TIMEOUT_SECONDS",
    "TRACE_AGENT_WEB_SEARCH_PROVIDER",
    "TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS",
    "TRACE_AGENT_DEVICE_UDID",
)
_INPUT_OUTPUT_ERROR: Final = 5
_BOOTSTRAP_ATTEMPTS: Final = 6
_BOOTSTRAP_RETRY_SECONDS: Final = 0.5
_UNLOAD_ATTEMPTS: Final = 40
_UNLOAD_RETRY_SECONDS: Final = 0.25
_MIN_UPDATER_INTERVAL_SECONDS: Final = 300
_MIN_UPDATE_ARGUMENTS: Final = 3


@dataclass(frozen=True, slots=True)
class MacWorkerLaunchd:
    executable: Path
    agent_home: Path
    plist_path: Path
    codex_executable: Path | None = None
    install_root: Path | None = None

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{_WORKER_LABEL}"

    def install(self) -> None:
        if self.codex_executable is None:
            message = "Codex executable is required when installing the worker service"
            raise ValueError(message)
        home = self.agent_home.expanduser().resolve()
        stdout, stderr = _log_paths(home, "marketing-worker")
        environment = _launchd_environment(
            home,
            self.codex_executable,
            install_root=self.install_root,
        )
        payload: dict[str, str | bool | list[str] | dict[str, str]] = {
            "Label": _WORKER_LABEL,
            "ProgramArguments": [
                str(self.executable.expanduser().absolute()),
                "worker",
                "service",
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "EnvironmentVariables": environment,
            "StandardOutPath": str(stdout),
            "StandardErrorPath": str(stderr),
        }
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.plist_path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
        temporary.chmod(0o600)
        _ = temporary.replace(self.plist_path)

    def installed_codex_executable(self) -> Path | None:
        try:
            raw_payload = cast("object", plistlib.loads(self.plist_path.read_bytes()))
        except OSError, plistlib.InvalidFileException:
            return None
        if not isinstance(raw_payload, dict):
            return None
        payload = cast("dict[object, object]", raw_payload)
        raw_environment = payload.get("EnvironmentVariables")
        if not isinstance(raw_environment, dict):
            return None
        environment = cast("dict[object, object]", raw_environment)
        value = environment.get("TRACE_CODEX_BIN")
        return Path(value).expanduser() if isinstance(value, str) and value else None

    def start(self) -> subprocess.CompletedProcess[str]:
        return _start(self.domain, self.plist_path)

    def stop(self) -> subprocess.CompletedProcess[str]:
        return _stop(self.target)

    def status(self) -> subprocess.CompletedProcess[str]:
        return _status(self.target)

    def wait_until_stopped(self) -> bool:
        return _wait_until_stopped(self.target)

    def owns_installed_plist(self) -> bool:
        payload = _read_plist(self.plist_path)
        if payload is None or payload.get("Label") != _WORKER_LABEL:
            return False
        arguments = _plist_arguments(payload)
        return (
            arguments is not None
            and len(arguments) >= _MIN_UPDATE_ARGUMENTS
            and Path(arguments[0]).name == "trace-marketing"
            and arguments[1:3] == ["worker", "service"]
        )


@dataclass(frozen=True, slots=True)
class MacWorkerUpdaterLaunchd:
    executable: Path
    agent_home: Path
    install_root: Path
    plist_path: Path
    codex_executable: Path
    uv_executable: Path
    interval_seconds: int = 3600

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{_UPDATER_LABEL}"

    def install(self) -> None:
        if self.interval_seconds < _MIN_UPDATER_INTERVAL_SECONDS:
            message = "updater interval must be at least 300 seconds"
            raise ValueError(message)
        home = self.agent_home.expanduser().resolve()
        install_root = self.install_root.expanduser().resolve()
        stdout, stderr = _log_paths(home, "marketing-updater")
        environment = _launchd_environment(home, self.codex_executable, install_root=install_root)
        payload: dict[str, str | int | bool | list[str] | dict[str, str]] = {
            "Label": _UPDATER_LABEL,
            "ProgramArguments": [
                str(self.executable.expanduser().absolute()),
                "worker",
                "update",
                "--apply",
                "--home",
                str(home),
                "--install-root",
                str(install_root),
                "--uv",
                str(self.uv_executable.expanduser().resolve()),
            ],
            "RunAtLoad": True,
            "StartInterval": self.interval_seconds,
            "ProcessType": "Background",
            "EnvironmentVariables": environment,
            "StandardOutPath": str(stdout),
            "StandardErrorPath": str(stderr),
        }
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.plist_path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
        temporary.chmod(0o600)
        _ = temporary.replace(self.plist_path)

    def start(self) -> subprocess.CompletedProcess[str]:
        return _start(self.domain, self.plist_path)

    def stop(self) -> subprocess.CompletedProcess[str]:
        return _stop(self.target)

    def status(self) -> subprocess.CompletedProcess[str]:
        return _status(self.target)

    def wait_until_stopped(self) -> bool:
        return _wait_until_stopped(self.target)

    def owns_installed_plist(self) -> bool:
        payload = _read_plist(self.plist_path)
        if payload is None or payload.get("Label") != _UPDATER_LABEL:
            return False
        text_arguments = _plist_arguments(payload)
        return (
            text_arguments is not None
            and len(text_arguments) >= _MIN_UPDATE_ARGUMENTS
            and text_arguments[0] == str(self.executable.expanduser().absolute())
            and text_arguments[1:3] == ["worker", "update"]
        )


def default_worker_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_WORKER_LABEL}.plist"


def default_updater_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_UPDATER_LABEL}.plist"


def _launchd_environment(
    home: Path,
    codex_executable: Path,
    *,
    install_root: Path | None = None,
) -> dict[str, str]:
    environment = {
        "TRACE_AGENT_HOME": str(home),
        "TRACE_CODEX_BIN": str(codex_executable.expanduser().resolve()),
        "PATH": os.environ.get("PATH", _DEFAULT_PATH),
    }
    if install_root is not None:
        environment["TRACE_MARKETING_INSTALL_ROOT"] = str(install_root.expanduser().resolve())
    environment.update(
        {name: value for name in _WORKER_ENV_ALLOWLIST if (value := os.environ.get(name))}
    )
    return environment


def _read_plist(path: Path) -> dict[str, object] | None:
    try:
        raw_payload = cast("object", plistlib.loads(path.read_bytes()))
    except OSError, plistlib.InvalidFileException:
        return None
    if not isinstance(raw_payload, dict):
        return None
    return cast("dict[str, object]", raw_payload)


def _plist_arguments(payload: dict[str, object]) -> list[str] | None:
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list):
        return None
    raw_arguments = cast("list[object]", arguments)
    if not all(isinstance(item, str) for item in raw_arguments):
        return None
    return cast("list[str]", raw_arguments)


def _log_paths(home: Path, stem: str) -> tuple[Path, Path]:
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs.chmod(0o700)
    stdout = logs / f"{stem}.stdout.log"
    stderr = logs / f"{stem}.stderr.log"
    for path in (stdout, stderr):
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    return stdout, stderr


def _start(domain: str, plist_path: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.CompletedProcess[str]([], 1, stdout="", stderr="")
    for attempt in range(_BOOTSTRAP_ATTEMPTS):
        result = subprocess.run(  # noqa: S603
            (_LAUNCHCTL, "bootstrap", domain, str(plist_path)),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        detail = f"{result.stdout}\n{result.stderr}"
        if result.returncode != _INPUT_OUTPUT_ERROR or "Input/output error" not in detail:
            return result
        if attempt + 1 < _BOOTSTRAP_ATTEMPTS:
            time.sleep(_BOOTSTRAP_RETRY_SECONDS)
    return result


def _stop(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (_LAUNCHCTL, "bootout", target),
        check=False,
        capture_output=True,
        text=True,
    )


def _status(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (_LAUNCHCTL, "print", target),
        check=False,
        capture_output=True,
        text=True,
    )


def _wait_until_stopped(target: str) -> bool:
    for attempt in range(_UNLOAD_ATTEMPTS):
        if _status(target).returncode != 0:
            return True
        if attempt + 1 < _UNLOAD_ATTEMPTS:
            time.sleep(_UNLOAD_RETRY_SECONDS)
    return False
