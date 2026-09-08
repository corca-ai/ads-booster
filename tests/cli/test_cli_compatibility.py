from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import TypedDict

from click import unstyle
from pydantic import TypeAdapter
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from ads_booster.cli.marketing import app as marketing_app


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


def test_retired_worker_command_is_unavailable() -> None:
    result = CliRunner().invoke(marketing_app, ["worker", "--help"])

    assert result.exit_code == 2
    root = unstyle(CliRunner().invoke(marketing_app, ["--help"]).stdout)
    assert all(command in root for command in ("version", "agent", "service", "server"))
    # Match command names, not substrings: the supported "server" includes retired "serve".
    command_group = get_command(marketing_app)
    assert isinstance(command_group, TyperGroup)
    commands = command_group.commands
    assert all(
        command not in commands
        for command in (
            "worker",
            "bridge",
            "simulate",
            "serve",
            "workspace",
            "capture",
            "compose",
            "run",
        )
    )


def test_marketing_agent_help_exposes_research_without_hosted_launch() -> None:
    result = CliRunner().invoke(marketing_app, ["agent", "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "research" in output
    assert "launch" not in output
    assert all(command not in output for command in ("publish", "capture", "spend", "outreach"))

    research = CliRunner().invoke(marketing_app, ["agent", "research", "--help"])
    research_output = unstyle(research.stdout)
    assert research.exit_code == 0
    assert all(option in research_output for option in ("--input", "--home", "--model"))

    launch = CliRunner().invoke(marketing_app, ["agent", "launch", "--help"])
    assert launch.exit_code == 2


def test_marketing_service_help_exposes_on_prem_owner_without_appium_dependency() -> None:
    result = CliRunner().invoke(marketing_app, ["service", "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert all(command in output for command in ("doctor", "run"))
    doctor = CliRunner().invoke(marketing_app, ["service", "doctor"])
    assert doctor.exit_code == 0
    report = TypeAdapter(dict[str, object]).validate_json(doctor.stdout)
    assert report["canonical_run_owner"] == "on_prem_marketing_agent_service"
    assert report["appium_required"] is False


def test_service_doctor_does_not_create_state_directories(tmp_path: Path) -> None:
    home = tmp_path / "uninstalled"
    result = CliRunner().invoke(marketing_app, ["service", "doctor", "--home", str(home)])
    assert result.exit_code == 0
    assert not home.exists()
