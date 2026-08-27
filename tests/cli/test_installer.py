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
    assert "--tag" in result.stdout
    assert "--install-root" in result.stdout
    assert "--uv" in result.stdout
    assert "immutable" in result.stdout
    assert "Appium" in result.stdout
    assert "Xcode" in result.stdout
    assert "Codex CLI" in result.stdout
    assert "fresh Mac may install before enrollment" in result.stdout


def test_installer_dry_run_prints_managed_release_plan(tmp_path: Path) -> None:
    install_root = tmp_path / "managed"
    agent_home = tmp_path / "agent"
    result = run_installer(
        "--dry-run",
        "--tag",
        "v1.2.3",
        "--home",
        str(agent_home),
        "--install-root",
        str(install_root),
    )

    assert result.returncode == 0
    assert "v1.2.3" in result.stdout
    assert "stable + immutable + tag/commit + SHA-256 + GitHub attestations" in result.stdout
    assert str(agent_home) in result.stdout
    assert str(install_root / "releases" / "<version>") in result.stdout
    assert "separate worker and pull updater LaunchAgents" in result.stdout
    assert "Codex CLI, Xcode, Appium, XCUITest, Trace app upgrades" in result.stdout
    assert not install_root.exists()


def test_installer_rejects_mutable_or_in_place_sources(tmp_path: Path) -> None:
    result = run_installer(
        "--dry-run",
        "--source",
        ".",
    )

    assert result.returncode == 1
    assert "unsafe for production" in result.stderr
    assert "immutable release" in result.stderr
    assert not (tmp_path / "bin").exists()


def test_installer_rejects_non_semantic_release_tag(tmp_path: Path) -> None:
    result = run_installer(
        "--dry-run",
        "--tag",
        "main",
        "--install-root",
        str(tmp_path / "managed"),
    )

    assert result.returncode == 1
    assert "strict semantic versioning" in result.stderr


def test_production_installer_has_no_mutable_or_in_place_uv_update_path() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    bootstrap = (REPOSITORY_ROOT / "scripts" / "bootstrap-mac-worker.py").read_text(
        encoding="utf-8"
    )

    assert "uv tool install" not in installer
    assert "--force" not in installer
    assert "TRACE_ADS_REF" not in installer
    assert "gh release download" in installer
    assert '"gh", "release", "verify"' in bootstrap
    assert '"gh", "release", "verify-asset"' in bootstrap
