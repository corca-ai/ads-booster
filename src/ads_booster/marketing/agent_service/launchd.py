"""Per-user launchd lifecycle for the canonical Marketing Agent Service."""

from __future__ import annotations

import os
import plistlib
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

_LABEL: Final = "com.corca.trace-marketing-agent"
_LAUNCHCTL: Final = "/bin/launchctl"
_DEFAULT_PATH: Final = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_MIN_ARGUMENTS: Final = 3


@dataclass(frozen=True, slots=True)
class MarketingAgentLaunchd:
    executable: Path
    agent_home: Path
    plist_path: Path
    codex_executable: Path
    model: str
    port: int = 8765
    tenant: str = "trace"
    principal: str = "local-operator"

    @property
    def domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def target(self) -> str:
        return f"{self.domain}/{_LABEL}"

    @property
    def token_path(self) -> Path:
        return self.agent_home.expanduser().resolve() / "marketing-agent" / "service-token"

    def install(self) -> str:
        """Write the token outside the plist and install an owned definition."""
        home = self.agent_home.expanduser().resolve()
        token = self._ensure_token()
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        logs.chmod(0o700)
        stdout = logs / "marketing-agent.stdout.log"
        stderr = logs / "marketing-agent.stderr.log"
        for path in (stdout, stderr):
            path.touch(mode=0o600, exist_ok=True)
            path.chmod(0o600)
        payload: dict[str, object] = {
            "Label": _LABEL,
            "ProgramArguments": [
                str(self.executable.expanduser().absolute()),
                "service",
                "daemon",
                "--model",
                self.model,
                "--home",
                str(home),
                "--port",
                str(self.port),
                "--tenant",
                self.tenant,
                "--principal",
                self.principal,
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "EnvironmentVariables": {
                "TRACE_CODEX_BIN": str(self.codex_executable.expanduser().resolve()),
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
        return token

    def start(self) -> subprocess.CompletedProcess[str]:
        return _launchctl("bootstrap", self.domain, str(self.plist_path))

    def stop(self) -> subprocess.CompletedProcess[str]:
        return _launchctl("bootout", self.target)

    def status(self) -> subprocess.CompletedProcess[str]:
        return _launchctl("print", self.target)

    def owns_installed_plist(self) -> bool:
        try:
            payload = cast("object", plistlib.loads(self.plist_path.read_bytes()))
        except OSError, plistlib.InvalidFileException:
            return False
        if not isinstance(payload, dict):
            return False
        typed_payload = cast("dict[object, object]", payload)
        if typed_payload.get("Label") != _LABEL:
            return False
        arguments = typed_payload.get("ProgramArguments")
        if not isinstance(arguments, list):
            return False
        raw_arguments = cast("list[object]", arguments)
        return (
            len(raw_arguments) >= _MIN_ARGUMENTS
            and all(isinstance(item, str) for item in raw_arguments)
            and Path(cast("str", raw_arguments[0])).name == "trace-marketing"
            and raw_arguments[1:3] == ["service", "daemon"]
        )

    def _ensure_token(self) -> str:
        path = self.token_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                path.chmod(0o600)
                return token
        token = secrets.token_urlsafe(32)
        _ = path.write_text(token + "\n", encoding="utf-8")
        path.chmod(0o600)
        return token


def default_service_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _launchctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (_LAUNCHCTL, *arguments), check=False, capture_output=True, text=True
    )


__all__ = ["MarketingAgentLaunchd", "default_service_plist_path"]
