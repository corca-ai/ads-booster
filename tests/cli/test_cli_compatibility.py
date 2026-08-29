from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from click import unstyle
from pydantic import TypeAdapter
from typer.testing import CliRunner

from ads_booster.cli.marketing import app as marketing_app

if TYPE_CHECKING:
    import pytest


class ProjectTable(TypedDict):
    name: str
    scripts: dict[str, str]


class PyprojectTable(TypedDict):
    project: ProjectTable


def test_project_exposes_only_the_trace_marketing_console_script() -> None:
    pyproject = TypeAdapter(PyprojectTable).validate_python(
        tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    )
    project = pyproject["project"]

    assert project["name"] == "trace-appium-capture"
    assert project["scripts"] == {"trace-marketing": "ads_booster.cli.marketing:app"}


def test_package_source_parses_on_the_declared_python_314_floor() -> None:
    package_root = Path(__file__).parents[2] / "src" / "ads_booster"

    for source in sorted(package_root.rglob("*.py")):
        _ = ast.parse(
            source.read_text(encoding="utf-8"),
            filename=str(source),
            feature_version=(3, 14),
        )


def test_marketing_worker_help_exposes_the_replaceable_mac_lifecycle() -> None:
    result = CliRunner().invoke(marketing_app, ["worker", "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert all(
        command in output
        for command in (
            "create-enrollment",
            "enroll",
            "doctor",
            "run",
            "install-service",
            "status",
            "update",
            "finish-bootstrap",
            "updater-status",
            "set-state",
            "revoke",
            "list",
        )
    )
    root = unstyle(CliRunner().invoke(marketing_app, ["--help"]).stdout)
    assert all(command in root for command in ("version", "worker"))
    assert all(
        command not in root
        for command in (
            "bridge",
            "simulate",
            "serve",
            "workspace",
            "capture",
            "compose",
            "run",
        )
    )


def test_worker_stop_treats_an_already_missing_launchd_service_as_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingLaunchdService:
        def stop(self) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["launchctl", "bootout"],
                returncode=3,
                stdout="",
                stderr="Boot-out failed: No such process",
            )

        def wait_until_stopped(self) -> bool:
            return True

    missing = MissingLaunchdService()

    def launchd_for(_home: Path) -> MissingLaunchdService:
        return missing

    monkeypatch.setattr("ads_booster.cli.marketing._worker_launchd", launchd_for)

    result = CliRunner().invoke(marketing_app, ["worker", "stop", "--home", str(tmp_path)])

    assert result.exit_code == 0
    assert "worker service: stopped" in result.stdout
