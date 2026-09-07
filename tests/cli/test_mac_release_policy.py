# ruff: noqa: S603, S607 - fixed local CLI and fixture Git commands, never a shell.
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

POLICY = Path(__file__).resolve().parents[2] / "scripts/mac-release-policy.py"
WORKFLOW = POLICY.parent.parent / ".github/workflows/release-mac-worker.yml"


@pytest.mark.parametrize(
    ("event", "version", "expected"),
    [
        ("pull_request", "1.2.3", "false"),
        ("push", "1.2.3", "false"),
        ("pull_request", "1.2.4", "true"),
        ("push", "1.2.4", "true"),
        ("workflow_dispatch", "1.2.3", "true"),
    ],
)
def test_release_requires_version_change_or_explicit_dispatch(
    tmp_path: Path, event: str, version: str, expected: str
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    _ = git("init", "-q")
    project = tmp_path / "pyproject.toml"
    _ = project.write_text('[project]\nversion = "1.2.3"\n')
    _ = git("add", "pyproject.toml")
    _ = git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    _ = project.write_text(f'[project]\nversion = "{version}"\n')
    result = subprocess.run(
        [sys.executable, str(POLICY), "--event", event, "--base", base],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == expected

    # Execute the workflow's real identity step against an already-owned version fixture.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _ = shutil.copy2(POLICY, scripts / POLICY.name)
    _ = (scripts / "github-release-state.py").write_text(
        'from pathlib import Path\nPath("state-checked").touch()\nraise SystemExit(1)\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    uname = bin_dir / "uname"
    _ = uname.write_text("#!/bin/sh\necho arm64\n")
    uname.chmod(0o755)
    output = tmp_path / "output"
    identity = WORKFLOW.read_text().split("      - name: Resolve and validate release identity\n")[
        1
    ]
    shell = textwrap.dedent(identity.split("        run: |\n")[1].split("\n      - name:")[0])
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_EVENT": event,
        "RELEASE_BASE": base,
        "RELEASE_SHA": base,
        "RELEASE_REPOSITORY": "corca-ai/ads-booster",
        "GITHUB_OUTPUT": str(output),
    }
    identity_result = subprocess.run(
        ["bash", "-c", shell], cwd=tmp_path, env=env, text=True, capture_output=True, check=False
    )
    assert (identity_result.returncode == 0) == (expected == "false")
    assert (tmp_path / "state-checked").exists() == (expected == "true")
    assert f"release_requested={expected}" in output.read_text()
    assert f"version={version}" in output.read_text()


@pytest.mark.parametrize("base", ["", "0" * 40, "a" * 40, "--help"])
def test_unavailable_base_cannot_authorize_a_release(tmp_path: Path, base: str) -> None:
    _ = (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    result = subprocess.run(
        [sys.executable, str(POLICY), "--event", "push", f"--base={base}"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout.strip() != "true"


def test_workflow_publication_depends_on_release_policy_output() -> None:
    workflow = WORKFLOW.read_text()
    assert "&& needs.check.outputs.release_requested == 'true'" in workflow
    assert "release_requested: ${{ steps.identity.outputs.release_requested }}" in workflow
    assert "github.event.pull_request.base.sha || github.event.before" in workflow
    assert "fetch-depth: 0" in workflow
