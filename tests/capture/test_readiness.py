from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.capture.readiness import CommandResult, DefaultCaptureReadiness
from ads_booster.contracts.models import DeviceKind, DeviceTarget

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True, slots=True)
class ReadinessCommandRunner:
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        if command == ("xcrun", "simctl", "list", "devices", "booted", "--json"):
            return CommandResult(stdout="{}", returncode=0)
        if command[:3] == ("xcrun", "simctl", "listapps"):
            return CommandResult(stdout="com.corca.Trace", returncode=0)
        return CommandResult(stdout="", returncode=0)


@dataclass(frozen=True, slots=True)
class ProcessStub:
    pass


def test_readiness_when_simulator_and_appium_are_inactive_then_it_starts_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ReadinessCommandRunner()
    appium_ready = iter((False, True))
    popen_calls: list[list[str]] = []

    def fake_appium_ready(_readiness: DefaultCaptureReadiness) -> bool:
        return next(appium_ready, True)

    def fake_which(_name: str) -> str:
        return "/bin/appium"

    def fake_popen(command: list[str], **_kwargs: bool | int | None) -> ProcessStub:
        popen_calls.append(command)
        return ProcessStub()

    monkeypatch.setattr(DefaultCaptureReadiness, "_appium_ready", fake_appium_ready)
    monkeypatch.setattr("ads_booster.capture.readiness.shutil.which", fake_which)
    monkeypatch.setattr("ads_booster.capture.readiness.subprocess.Popen", fake_popen)
    readiness = DefaultCaptureReadiness(
        appium_server="http://127.0.0.1:4723",
        command_runner=runner,
        poll_interval_seconds=0.001,
    )
    udid = "E1FB798D-79E6-4B25-A987-D298A4FD122A"

    readiness.ensure(
        DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid=udid,
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
        CaptureControl.start(timeout_seconds=2),
    )

    assert ("open", "-a", "Simulator") in runner.commands
    assert ("xcrun", "simctl", "boot", udid) in runner.commands
    assert ("xcrun", "simctl", "bootstatus", udid, "-b") in runner.commands
    assert popen_calls == [["/bin/appium", "--port", "4723"]]
