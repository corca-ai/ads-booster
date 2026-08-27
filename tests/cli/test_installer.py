from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPOSITORY_ROOT / "install.sh"
ProjectTable = TypedDict("ProjectTable", {"requires-python": str})


class PyprojectTable(TypedDict):
    project: ProjectTable


def run_installer(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(INSTALLER), *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_package_requires_the_python_version_used_by_its_source_syntax() -> None:
    # Given the package metadata consumed by uv tool installation
    metadata = TypeAdapter(PyprojectTable).validate_python(
        tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    )

    # When the supported Python range is inspected
    requires_python = metadata["project"]["requires-python"]

    # Then uv cannot select Python 3.13 for source that requires Python 3.14 syntax
    assert requires_python == ">=3.14,<3.15"


def test_installer_help_describes_native_install_controls() -> None:
    result = run_installer("--help")

    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--source" in result.stdout
    assert "--ref" in result.stdout
    assert "--no-shell-update" in result.stdout
    assert "--workspace-service" in result.stdout
    assert "--no-workspace-service" in result.stdout
    assert "--no-cloudflared-install" in result.stdout
    assert "--workspace-name" in result.stdout
    assert "Appium" in result.stdout
    assert "Xcode" in result.stdout


def test_installer_dry_run_prints_user_local_tool_plan(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    result = run_installer(
        "--dry-run",
        "--source",
        ".",
        "--bin-dir",
        str(bin_dir),
        "--no-shell-update",
    )

    assert result.returncode == 0
    assert "uv tool install" in result.stdout
    assert "trace-appium-capture" in result.stdout
    assert "trace-ads" in result.stdout
    assert str(bin_dir) in result.stdout
    assert "workspace service: not started" in result.stdout
    assert "cloudflared" not in result.stdout
    assert not bin_dir.exists()


def test_installer_dry_run_can_explicitly_start_workspace_service(tmp_path: Path) -> None:
    result = run_installer(
        "--dry-run",
        "--source",
        ".",
        "--workspace-service",
        "--workspace-name",
        "Launch archive",
        "--bin-dir",
        str(tmp_path / "bin"),
        "--no-shell-update",
    )

    assert result.returncode == 0
    assert "workspace service: macOS launchd + cloudflared tunnel" in result.stdout
    assert "workspace name: Launch archive" in result.stdout


def test_installer_explicit_ref_uses_remote_source_from_a_checkout(tmp_path: Path) -> None:
    result = run_installer(
        "--dry-run",
        "--ref",
        "v0.1.0",
        "--bin-dir",
        str(tmp_path / "bin"),
        "--no-shell-update",
    )

    assert result.returncode == 0
    assert "git+https://github.com/corca-ai/ads-booster.git@v0.1.0" in result.stdout
