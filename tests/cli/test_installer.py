from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPOSITORY_ROOT / "install.sh"
BOOTSTRAP = REPOSITORY_ROOT / "scripts" / "bootstrap-mac-worker.py"
README = REPOSITORY_ROOT / "README.md"
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
    assert "provenance-verified" in result.stdout
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
    assert "stable + tag/commit + SHA-256 + workflow-bound attestations" in result.stdout
    assert str(agent_home) in result.stdout
    assert str(install_root / "releases" / "<version>") in result.stdout
    assert "separate worker and pull updater LaunchAgents" in result.stdout
    assert "Codex CLI, Xcode, Appium, XCUITest, Trace app upgrades" in result.stdout
    assert not install_root.exists()


def test_installer_rejects_branch_or_in_place_sources(tmp_path: Path) -> None:
    result = run_installer(
        "--dry-run",
        "--source",
        ".",
    )

    assert result.returncode == 1
    assert "unsafe for production" in result.stderr
    assert "versioned release" in result.stderr
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
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    assert "uv tool install" not in installer
    assert "--force" not in installer
    assert "TRACE_ADS_REF" not in installer
    assert "TRACE_ADS_REPOSITORY" not in installer
    assert "--repository" not in installer
    assert "gh release download" in installer
    assert '"attestation",' in bootstrap
    assert '"--signer-workflow",' in bootstrap
    assert '"--source-digest",' in bootstrap
    assert '"--deny-self-hosted-runners",' in bootstrap
    assert '"release", "verify"' not in bootstrap
    assert '"release", "verify-asset"' not in bootstrap


def test_bootstrap_has_a_fixed_repository_and_quotes_unenrolled_continuation(
    tmp_path: Path,
) -> None:
    help_result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "--repository" not in help_result.stdout

    spec = importlib.util.spec_from_file_location("trace_bootstrap_fixture", BOOTSTRAP)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = tuple(tmp_path / name for name in ("bin with space", "home", "root", "uv", "gh"))

    command = module.finish_bootstrap_command(*values)

    assert shlex.split(command) == [
        str(values[0]),
        "worker",
        "finish-bootstrap",
        "--home",
        str(values[1]),
        "--install-root",
        str(values[2]),
        "--uv",
        str(values[3]),
        "--gh",
        str(values[4]),
    ]
    assert "<origin>" not in command


def _failed_attestation_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    marker = tmp_path / "executed"
    bundle_name = "trace-marketing-macos-arm64-v1.2.3.tar.gz"
    _ = (fixture / "trace-marketing-release.json").write_text(
        json.dumps(
            {
                "tag": "v1.2.3",
                "commit_sha": "a" * 40,
                "bundle": {"name": bundle_name},
            }
        ),
        encoding="utf-8",
    )
    _ = (fixture / "trace-marketing-bootstrap.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    _ = (fixture / bundle_name).write_bytes(b"fixture")
    fixture_bin = tmp_path / "bin"
    fixture_bin.mkdir()
    fake_gh = fixture_bin / "gh"
    _ = fake_gh.write_text(
        """#!/bin/bash
set -euo pipefail
if [[ "$1 $2 $3" == "attestation verify --help" ]]; then exit 0; fi
if [[ "$1 $2" == "release view" ]]; then printf 'v1.2.3\\n'; exit 0; fi
if [[ "$1 $2" == "release download" ]]; then
  destination=""; pattern=""
  while (($#)); do
    if [[ "$1" == "--dir" ]]; then destination="$2"; shift 2; continue; fi
    if [[ "$1" == "--pattern" ]]; then
      pattern="$2"; cp "$TRACE_TEST_FIXTURE/$pattern" "$destination/$pattern"; shift 2; continue
    fi
    shift
  done
  exit 0
fi
if [[ "$1 $2" == "attestation verify" ]]; then
  [[ "$3" != *trace-marketing-bootstrap.py ]]
  exit
fi
exit 2
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)
    fake_uname = fixture_bin / "uname"
    _ = fake_uname.write_text(
        '#!/bin/bash\nif [[ "$1" == "-s" ]]; then echo Darwin; else echo arm64; fi\n',
        encoding="utf-8",
    )
    fake_uname.chmod(0o700)
    fake_uv = fixture_bin / "uv"
    _ = fake_uv.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o700)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{fixture_bin}:/usr/bin:/bin",
            "TRACE_TEST_FIXTURE": str(fixture),
            "TRACE_ADS_UV": str(fake_uv),
            "TRACE_AGENT_HOME": str(tmp_path / "agent"),
            "TRACE_MARKETING_INSTALL_ROOT": str(tmp_path / "product"),
        }
    )
    return environment, marker


def test_installer_does_not_execute_a_bootstrap_with_failed_attestation(tmp_path: Path) -> None:
    environment, marker = _failed_attestation_environment(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(INSTALLER), "--tag", "v1.2.3"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_readme_bootstrap_is_one_fail_fast_unit_before_code_execution(tmp_path: Path) -> None:
    environment, marker = _failed_attestation_environment(tmp_path)
    readme = README.read_text(encoding="utf-8")
    heading = readme.index("## Bootstrap a verified Mac worker release")
    opening = readme.index("```bash\n", heading) + len("```bash\n")
    closing = readme.index("\n```", opening)
    command = readme[opening:closing]

    result = subprocess.run(
        ["/bin/bash"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input=command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()
