"""Disposable Ubuntu only: real installed CLI/systemd, fixture Slack identity and GitHub CI.

Never invokes Codex inference or Slack posting. Run under the dedicated container user.
"""

# pyright: reportAny=false, reportExplicitAny=false
# Dynamic installed manager and HTTP JSON are test boundaries.
# ruff: noqa: T201, INP001, S603
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.request import Request, urlopen

from ads_booster.cli import server


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


answers = [
    "https://agent.example.com",
    "fixture-model",
    "marketing",
    "A123",
    "C123",
    "U123",
    "U123",
]
with (
    patch.object(server, "interactive_terminal", return_value=True),
    patch.object(server, "prompt", side_effect=answers),
    patch("getpass.getpass", side_effect=["xoxb-fixture", "fixture-signing"]),
    patch("typer.confirm", return_value=False),
    patch.object(
        server,
        "json_request",
        return_value={"ok": True, "team_id": "T123", "user_id": "UBOT", "bot_id": "B123"},
    ),
):
    server.setup_config()
# Idempotent setup, real unit rendering and private file modes.
server.setup_config()
assert (server.CONFIG / "agent.env").stat().st_mode & 0o777 == 0o600
server.start()
_ = run("systemctl", "--user", "stop", server.TIMER)


def health() -> dict[str, Any]:
    for _attempt in range(60):
        try:
            with urlopen("http://127.0.0.1:8765/health", timeout=2) as response:
                value = json.load(response)
            if value.get("owner") == "on_prem_agent":
                return value
        except OSError:
            pass
        time.sleep(1)
    message = "Installed service did not become healthy"
    raise AssertionError(message)


initial = health()
assert initial["release"].startswith("source-")
# Exercise the real HTTP Slack signature/URL challenge boundary without external delivery.
body = json.dumps({"type": "url_verification", "challenge": "installed-fixture"}).encode()
timestamp = str(int(time.time()))
signature = (
    "v0="
    + hmac.new(
        b"fixture-signing", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
)
request = Request(
    "http://127.0.0.1:8765/channels/slack/events",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": signature,
    },
)
with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed loopback test server.
    assert json.load(response)["challenge"] == "installed-fixture"

# A fixture upstream advances main; only GitHub transport/CI trust are substituted.
# Git fetch, locked dependency install, drain, backup, service switch and health are real.
fixture = Path.home() / "upstream"
_ = run("git", "clone", "--no-local", "/srv/source", str(fixture))
_ = run("git", "-C", str(fixture), "checkout", "-B", "main")
_ = run(
    "git",
    "-C",
    str(fixture),
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@example.com",
    "commit",
    "--allow-empty",
    "-m",
    "Fixture main advance",
)
expected = run("git", "-C", str(fixture), "rev-parse", "HEAD")
spec = importlib.util.spec_from_file_location(
    "installed_manager", server.ROOT / "current/agent-manager.py"
)
assert spec is not None
assert spec.loader is not None
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)
command = manager.command


def fixture_command(args: list[str], **kwargs: object) -> str:
    args = [
        str(fixture) if arg == "https://github.com/corca-ai/ads-booster.git" else arg
        for arg in args
    ]
    return command(args, **kwargs)


with (
    patch.object(manager, "command", side_effect=fixture_command),
    patch.object(
        manager,
        "github_checks",
        return_value=[
            {
                "check_runs": [
                    {
                        "name": "Verify on-prem agent",
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        ],
    ),
):
    candidate = manager.stage(server.ROOT)
    assert candidate is not None
    manager.activate(server.ROOT, candidate)
    assert manager.stage(server.ROOT) is None
assert health()["release"] == expected
_ = run("systemctl", "--user", "restart", server.SERVICE)
assert health()["release"] == expected
_ = run("systemctl", "--user", "start", server.TIMER)
assert run("systemctl", "--user", "is-enabled", server.TIMER) == "enabled"
assert run("systemctl", "--user", "is-active", server.TIMER) == "active"
assert run("loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value") == "yes"
assert (server.ROOT / "last-success.json").is_file()
print(
    json.dumps(
        {
            "installed_lifecycle": "passed",
            "fixture_main_sha": expected,
            "live_slack_delivery": "not_tested",
        }
    )
)
