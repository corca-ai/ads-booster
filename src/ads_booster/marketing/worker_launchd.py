from __future__ import annotations

import os
import plistlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_LABEL: Final = "com.corca.trace-marketing-worker"
_LAUNCHCTL: Final = "/bin/launchctl"
_DEFAULT_PATH: Final = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_INPUT_OUTPUT_ERROR: Final = 5
_BOOTSTRAP_ATTEMPTS: Final = 6
_BOOTSTRAP_RETRY_SECONDS: Final = 0.5
_UNLOAD_ATTEMPTS: Final = 40
_UNLOAD_RETRY_SECONDS: Final = 0.25


@dataclass(frozen=True, slots=True)
class MacWorkerLaunchd:
    executable: Path
    agent_home: Path
    plist_path: Path

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{_LABEL}"

    def install(self) -> None:
        home = self.agent_home.expanduser().resolve()
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        logs.chmod(0o700)
        stdout = logs / "marketing-worker.stdout.log"
        stderr = logs / "marketing-worker.stderr.log"
        for path in (stdout, stderr):
            path.touch(mode=0o600, exist_ok=True)
            path.chmod(0o600)
        payload: dict[str, str | bool | list[str] | dict[str, str]] = {
            "Label": _LABEL,
            "ProgramArguments": [
                str(self.executable.expanduser().resolve()),
                "worker",
                "service",
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "EnvironmentVariables": {
                "TRACE_AGENT_HOME": str(home),
                "PATH": os.environ.get("PATH", _DEFAULT_PATH),
            },
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
        result = subprocess.CompletedProcess[str]([], 1, stdout="", stderr="")
        for attempt in range(_BOOTSTRAP_ATTEMPTS):
            result = subprocess.run(  # noqa: S603
                (_LAUNCHCTL, "bootstrap", self.domain, str(self.plist_path)),
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

    def stop(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            (_LAUNCHCTL, "bootout", self.target),
            check=False,
            capture_output=True,
            text=True,
        )

    def status(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            (_LAUNCHCTL, "print", self.target),
            check=False,
            capture_output=True,
            text=True,
        )

    def wait_until_stopped(self) -> bool:
        for attempt in range(_UNLOAD_ATTEMPTS):
            if self.status().returncode != 0:
                return True
            if attempt + 1 < _UNLOAD_ATTEMPTS:
                time.sleep(_UNLOAD_RETRY_SECONDS)
        return False


def default_worker_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
