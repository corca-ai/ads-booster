from __future__ import annotations

import plistlib
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, override

_LABEL: Final = "com.corca.trace-agent"
_MAX_PORT: Final = 65_535
_LAUNCHCTL: Final = "/bin/launchctl"
_INPUT_OUTPUT_ERROR: Final = 5
_BOOTSTRAP_ATTEMPTS: Final = 6
_RETRY_DELAY_SECONDS: Final = 0.5
_UNLOAD_ATTEMPTS: Final = 40
_UNLOAD_DELAY_SECONDS: Final = 0.25


@dataclass(frozen=True, slots=True)
class LaunchdConfigError(Exception):
    issue: LaunchdConfigIssue

    @override
    def __str__(self) -> str:
        return self.issue.value


@unique
class LaunchdConfigIssue(StrEnum):
    HOST = "launchd service host must be 127.0.0.1"
    PORT = "launchd service port must be between 1 and 65535"
    TUNNEL = "launchd tunnel must be none or cloudflared"


@dataclass(frozen=True, slots=True)
class LaunchdConfig:
    executable: Path
    agent_home: Path
    host: str
    port: int
    tunnel: str
    cloudflared_path: Path | None = None

    def __post_init__(self) -> None:
        """Reject service definitions that could expose the ASGI origin."""
        if self.host != "127.0.0.1":
            raise LaunchdConfigError(LaunchdConfigIssue.HOST)
        if not 1 <= self.port <= _MAX_PORT:
            raise LaunchdConfigError(LaunchdConfigIssue.PORT)
        if self.tunnel not in {"none", "cloudflared"}:
            raise LaunchdConfigError(LaunchdConfigIssue.TUNNEL)


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def install_plist(config: LaunchdConfig, path: Path) -> None:
    home = config.agent_home.expanduser().resolve()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    logs.chmod(0o700)
    stdout = logs / "service.stdout.log"
    stderr = logs / "service.stderr.log"
    for log_path in (stdout, stderr):
        log_path.touch(mode=0o600, exist_ok=True)
        log_path.chmod(0o600)
    environment = {"TRACE_AGENT_HOME": str(home)}
    if config.cloudflared_path is not None:
        environment["TRACE_AGENT_CLOUDFLARED"] = str(config.cloudflared_path)
    payload: dict[str, str | bool | list[str] | dict[str, str]] = {
        "Label": _LABEL,
        "ProgramArguments": [
            str(config.executable.expanduser().resolve()),
            "serve",
            "--host",
            config.host,
            "--port",
            str(config.port),
            "--tunnel",
            config.tunnel,
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        plistlib.dump(payload, stream, sort_keys=True)
    temporary.chmod(0o600)
    _ = temporary.replace(path)


def launchd_label() -> str:
    return _LABEL


def bootstrap_launchd_service(
    domain: str,
    target: Path,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.CompletedProcess[str]([], 1, stdout="", stderr="")
    for attempt in range(_BOOTSTRAP_ATTEMPTS):
        result = subprocess.run(  # noqa: S603
            [_LAUNCHCTL, "bootstrap", domain, str(target)],
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
            time.sleep(_RETRY_DELAY_SECONDS)
    return result


def stop_launchd_service(domain: str, label: str) -> bool:
    target = f"{domain}/{label}"
    _ = subprocess.run(  # noqa: S603
        [_LAUNCHCTL, "bootout", target],
        check=False,
        capture_output=True,
        text=True,
    )
    for attempt in range(_UNLOAD_ATTEMPTS):
        probe = subprocess.run(  # noqa: S603
            [_LAUNCHCTL, "print", target],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            return True
        if attempt + 1 < _UNLOAD_ATTEMPTS:
            time.sleep(_UNLOAD_DELAY_SECONDS)
    return False
