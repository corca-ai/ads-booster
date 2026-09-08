"""Opt-in worker command composition does not install or activate devices on help/doctor."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ads_booster.cli import remote_capture
from ads_booster.cli.marketing import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ads_booster.transport.json_types import JsonObject


class Worker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def doctor(self) -> JsonObject:
        self.calls.append("doctor")
        return {"ready": False, "reason_code": "fixture_unavailable"}

    def work_once(self) -> JsonObject:
        self.calls.append("work_once")
        return {"state": "idle"}


def test_help_never_constructs_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(config: Path) -> None:
        _ = config
        message = "help instantiated worker"
        raise AssertionError(message)

    monkeypatch.setattr(remote_capture, "build_remote_worker", forbidden)
    for name in ("capture-remote-run", "capture-remote-doctor"):
        result = CliRunner().invoke(app, ["worker", name, "--help"])
        assert result.exit_code == 0
        assert "--config" in result.stdout


def test_doctor_and_once_are_separate_bounded_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "worker.json"
    _ = config.write_text("{}")
    worker = Worker()

    def factory(path: Path) -> Worker:
        assert path == config
        return worker

    monkeypatch.setattr(remote_capture, "build_remote_worker", factory)
    runner = CliRunner()
    doctor = runner.invoke(app, ["worker", "capture-remote-doctor", "--config", str(config)])
    assert doctor.exit_code == 1
    assert json.loads(doctor.stdout)["reason_code"] == "fixture_unavailable"
    assert worker.calls == ["doctor"]
    once = runner.invoke(app, ["worker", "capture-remote-run", "--config", str(config), "--once"])
    assert once.exit_code == 0
    assert json.loads(once.stdout) == {"state": "idle"}
    assert worker.calls == ["doctor", "work_once"]


def test_invalid_config_is_sanitized_without_state_creation(tmp_path: Path) -> None:
    config = tmp_path / "secret-token.json"
    _ = config.write_text('{"token": "never-print-this"}')
    result = CliRunner().invoke(app, ["worker", "capture-remote-doctor", "--config", str(config)])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ready": False,
        "error": "remote_capture_configuration_invalid",
    }
    assert "never-print-this" not in result.stdout
    assert sorted(path.name for path in tmp_path.iterdir()) == [config.name]
