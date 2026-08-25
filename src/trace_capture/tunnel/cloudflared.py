from __future__ import annotations

import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from pathlib import Path

_PUBLIC_URL: Final = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


@dataclass(frozen=True, slots=True)
class TunnelStartResult:
    public_url: str | None
    process: subprocess.Popen[str] | None
    detail: str


@final
class CloudflaredTunnel:
    binary: Path | None
    log_path: Path
    timeout_seconds: float

    def __init__(
        self,
        binary: Path | None,
        log_path: Path,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Configure a bounded quick-tunnel process without shell expansion."""
        self.binary = binary
        self.log_path = log_path
        self.timeout_seconds = timeout_seconds

    def start(self, local_url: str) -> TunnelStartResult:
        if self.binary is None:
            return TunnelStartResult(
                public_url=None,
                process=None,
                detail="cloudflared is not installed; local access only",
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        process = subprocess.Popen[str](
            [
                str(self.binary),
                "tunnel",
                "--config",
                "/dev/null",
                "--no-autoupdate",
                "--url",
                local_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if process.stderr is None:
            process.terminate()
            return TunnelStartResult(None, None, "cloudflared produced no status stream")
        status_stream: IO[str] = process.stderr
        deadline = time.monotonic() + self.timeout_seconds
        with (
            self.log_path.open("a", encoding="utf-8") as log,
            selectors.DefaultSelector() as poller,
        ):
            _ = poller.register(status_stream, selectors.EVENT_READ)
            while time.monotonic() < deadline and process.poll() is None:
                events = poller.select(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
                if not events:
                    continue
                line = status_stream.readline()
                if not line:
                    continue
                _ = log.write(line)
                _ = log.flush()
                match = _PUBLIC_URL.search(line)
                if match is not None and process.poll() is None:
                    return TunnelStartResult(match.group(0), process, "cloudflared tunnel active")
        self.stop(process)
        return TunnelStartResult(None, None, "cloudflared did not provide a live public URL")

    @staticmethod
    def stop(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            _ = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _ = process.wait(timeout=5)
