# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Final, assert_never

import httpx2

from trace_capture.service.runtime import TunnelName
from trace_capture.service.state import ServiceStateError, ServiceStateStore
from trace_capture.transport.http import create_http_client

_HTTP_OK: Final = 200
_READY_TIMEOUT_SECONDS: Final = 30.0
_READY_POLL_SECONDS: Final = 0.25


def discovered_cloudflared_path(tunnel: TunnelName) -> Path | None:
    match tunnel:
        case TunnelName.NONE:
            return None
        case TunnelName.CLOUDFLARED:
            executable = shutil.which("cloudflared")
            return None if executable is None else Path(executable)
        case unreachable:
            assert_never(unreachable)


def wait_for_service_ready(
    home: Path,
    tunnel: TunnelName,
) -> tuple[str | None, str | None]:
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    last_local_url: str | None = None
    last_public_url: str | None = None
    while time.monotonic() < deadline:
        try:
            state = ServiceStateStore(home).load()
        except ServiceStateError:
            return last_local_url, last_public_url
        if state is not None:
            last_local_url = f"http://{state.host}:{state.port}"
            last_public_url = state.public_url if tunnel == TunnelName.CLOUDFLARED else None
            try:
                with create_http_client() as http:
                    local_ready = http.get(f"{last_local_url}/health", {}).status_code == _HTTP_OK
            except httpx2.HTTPError:
                local_ready = False
            match tunnel:
                case TunnelName.NONE:
                    if local_ready:
                        return last_local_url, None
                case TunnelName.CLOUDFLARED:
                    if local_ready and last_public_url is not None:
                        return last_local_url, last_public_url
                case unreachable:
                    assert_never(unreachable)
        time.sleep(_READY_POLL_SECONDS)
    return last_local_url, last_public_url
