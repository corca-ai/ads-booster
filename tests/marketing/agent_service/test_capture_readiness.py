"""Readiness is a bounded observation, never simulator or Appium activation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.simctl_command import CommandResult
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.marketing.agent_service.capture_readiness import CaptureReadinessProbe

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request

NOW = datetime(2026, 9, 7, tzinfo=UTC)
UDID = "A" * 36
DEVICE = DeviceTarget(
    kind=DeviceKind.SIMULATOR, udid=UDID, platform_version="18.0", device_name="synthetic simulator"
)


@dataclass
class Runner:
    booted: bool = True
    bundle: str = "com.corca.Trace"
    app_container: str = ""
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 3
        self.calls.append(command)
        if command[2] == "list":
            output = json.dumps(
                {
                    "devices": {
                        "test": [
                            {
                                "udid": UDID,
                                "state": "Booted" if self.booted else "Shutdown",
                                "isAvailable": True,
                            }
                        ]
                    }
                }
            )
        elif command[2] == "listapps":
            # Actual simctl output is OpenStep, which plistlib.loads does not accept.
            output = '{ "com.corca.Trace" = { CFBundleIdentifier = "com.corca.Trace"; }; }'
        else:
            assert command == (
                "/fake/xcrun",
                "simctl",
                "get_app_container",
                UDID,
                "com.corca.Trace",
                "app",
            )
            if self.bundle != "com.corca.Trace":
                return CommandResult(stdout="", returncode=1)
            output = self.app_container + "\n"
        return CommandResult(stdout=output, returncode=0)


@dataclass
class Response:
    data: bytes = b'{"value":{"ready":true}}'
    url: str = "http://127.0.0.1:4723/status"
    status: int = 200
    closed: bool = False

    def read(self, size: int = -1) -> bytes:
        assert size == 65536
        return self.data[:size]

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


@dataclass
class Opener:
    response: Response = field(default_factory=Response)
    calls: int = 0

    def __call__(self, request: Request, *, timeout: float) -> Response:
        assert request.full_url == "http://127.0.0.1:4723/status"
        assert request.get_method() == "GET"
        assert 0 < timeout <= 3
        self.calls += 1
        return self.response


def missing_command(name: str) -> str | None:
    _ = name
    return None


def probe(tmp_path: Path, runner: Runner, opener: Opener) -> CaptureReadinessProbe:
    codex = tmp_path / "codex"
    _ = codex.write_text("fixture executable, never launched")
    codex.chmod(0o700)
    app = tmp_path / "Trace Debug.app"
    app.mkdir(exist_ok=True)
    runner.app_container = str(app)
    return CaptureReadinessProbe(
        DEVICE,
        "http://127.0.0.1:4723",
        codex,
        runner=runner,
        opener=opener,
        platform_name="Darwin",
        lookup=lambda name: "/fake/" + name,
    )


def test_openstep_listapps_is_replaced_with_readonly_app_container(tmp_path: Path) -> None:
    runner, opener = Runner(), Opener()
    readiness = probe(tmp_path, runner, opener).check(NOW)
    assert readiness.ready
    assert readiness.max_age_seconds == 30
    assert runner.calls == [
        ("/fake/xcrun", "simctl", "list", "devices", "booted", "--json"),
        ("/fake/xcrun", "simctl", "get_app_container", UDID, "com.corca.Trace", "app"),
    ]
    assert opener.calls == 1
    assert opener.response.closed


@pytest.mark.parametrize(
    "scenario", ["linux", "missing_command", "missing_codex", "physical", "remote"]
)
def test_missing_prerequisites_never_touch_devices(tmp_path: Path, scenario: str) -> None:
    runner, opener = Runner(), Opener()
    check = probe(tmp_path, runner, opener)
    if scenario == "linux":
        check = replace(check, platform_name="Linux")
    elif scenario == "missing_command":
        check = replace(check, lookup=missing_command)
    elif scenario == "missing_codex":
        check = replace(check, codex_executable=tmp_path / "missing")
    elif scenario == "physical":
        check = replace(check, device=DEVICE.model_copy(update={"kind": DeviceKind.PHYSICAL}))
    else:
        check = replace(check, appium_server="https://untrusted.example")
    assert not check.check(NOW).ready
    assert runner.calls == []
    assert opener.calls == 0


@pytest.mark.parametrize(("ready", "bundle"), [(False, "com.corca.Trace"), (True, "unrelated")])
def test_unbooted_or_wrong_app_is_unavailable(tmp_path: Path, ready: bool, bundle: str) -> None:
    runner, opener = Runner(booted=ready, bundle=bundle), Opener()
    assert not probe(tmp_path, runner, opener).check(NOW).ready
    assert opener.calls == 0


@pytest.mark.parametrize(
    "response",
    [
        Response(data=b'{"value":{"ready":false}}'),
        Response(data=b'{"ready":true}'),
        Response(data=b"not-json"),
        Response(data=b"x" * 65536),
        Response(url="http://127.0.0.1:9999/status"),
        Response(status=302),
    ],
)
def test_status_must_be_exact_bounded_ready_response(tmp_path: Path, response: Response) -> None:
    runner, opener = Runner(), Opener(response=response)
    assert not probe(tmp_path, runner, opener).check(NOW).ready
    assert response.closed


def test_probe_errors_are_sanitized(tmp_path: Path) -> None:
    def failing_lookup(_name: str) -> str:
        message = "sensitive internal error"
        raise OSError(message)

    check = replace(probe(tmp_path, Runner(), Opener()), lookup=failing_lookup)
    readiness = check.check(NOW)
    assert readiness.reason_code == "capture_readiness_unavailable"
    assert "sensitive" not in readiness.model_dump_json()


@pytest.mark.parametrize(
    "container", ["", "relative/Trace.app", "/nonexistent/Trace.app", "\n/app\n/other"]
)
def test_app_container_must_be_one_existing_absolute_directory(
    tmp_path: Path, container: str
) -> None:
    runner, opener = Runner(), Opener()
    check = probe(tmp_path, runner, opener)
    runner.app_container = container
    assert not check.check(NOW).ready
    assert opener.calls == 0
