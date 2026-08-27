from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ads_booster.marketing.worker_update import (
    MacWorkerReleaseManifest,
    extract_release_bundle,
)

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build-mac-worker-release.py"
BOOTSTRAP = ROOT / "scripts" / "bootstrap-mac-worker.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-mac-worker.yml"


def test_release_builder_emits_deterministic_exact_three_asset_envelope(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _ = (wheelhouse / "trace_appium_capture-1.2.3-py3-none-any.whl").write_bytes(b"project")
    _ = (wheelhouse / "dependency-1.0-py3-none-any.whl").write_bytes(b"dependency")
    requirements = tmp_path / "requirements.lock"
    _ = requirements.write_text("dependency==1.0\n", encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"

    for output in (first, second):
        completed = _build(wheelhouse, requirements, output)
        assert completed.returncode == 0, completed.stderr

    assets = {path.name for path in first.iterdir()}
    assert assets == {
        "trace-marketing-release.json",
        "trace-marketing-bootstrap.py",
        "trace-marketing-macos-arm64-v1.2.3.tar.gz",
    }
    manifest = MacWorkerReleaseManifest.model_validate_json(
        (first / "trace-marketing-release.json").read_text(encoding="utf-8")
    )
    assert manifest.commit_sha == "a" * 40
    assert (
        manifest.bundle.sha256
        == MacWorkerReleaseManifest.model_validate_json(
            (second / "trace-marketing-release.json").read_text(encoding="utf-8")
        ).bundle.sha256
    )
    extract_release_bundle(
        (first / manifest.bundle.name).read_bytes(),
        tmp_path / "extracted",
    )


def test_release_builder_rejects_version_not_matching_project_wheel(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _ = (wheelhouse / "trace_appium_capture-1.2.2-py3-none-any.whl").write_bytes(b"project")
    requirements = tmp_path / "requirements.lock"
    _ = requirements.write_text("dependency==1.0\n", encoding="utf-8")

    completed = _build(wheelhouse, requirements, tmp_path / "output")

    assert completed.returncode != 0
    assert "one exact project wheel" in completed.stderr


def test_release_workflow_is_manual_arm64_attested_and_fails_without_immutability() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "runs-on: macos-14" in workflow
    assert "immutable-releases" in workflow
    assert "setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d" in workflow
    assert "attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a" in workflow
    assert 'gh release verify "$tag"' in workflow
    assert 'gh release verify-asset "$tag"' in workflow
    assert 'target "$RELEASE_SHA"' in workflow
    assert "SSH" not in workflow


def _build(
    wheelhouse: Path,
    requirements: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executes the repository-owned builder fixture.
        (
            sys.executable,
            str(BUILDER),
            "--version",
            "1.2.3",
            "--commit-sha",
            "a" * 40,
            "--wheelhouse",
            str(wheelhouse),
            "--requirements",
            str(requirements),
            "--bootstrap",
            str(BOOTSTRAP),
            "--output",
            str(output),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
