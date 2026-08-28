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
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-cloudflare.yml"
PROJECT = ROOT / "pyproject.toml"


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


def test_release_workflow_checks_pr_then_publishes_merged_main_automatically() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "inputs:" not in workflow
    assert "runs-on: macos-15" in workflow
    assert "macos-14" not in workflow
    assert "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'" in workflow
    assert "tomllib.load" in workflow
    assert "TRACE_IMMUTABLE_RELEASES_ENABLED" not in workflow
    assert "bump pyproject.toml before merge" in workflow
    assert "scripts/github-release-state.py" in workflow
    assert "state_args=()" not in workflow
    assert 'state_args=(\n            --repository "$RELEASE_REPOSITORY"' in workflow
    assert "--repair-managed-partials" in workflow
    assert workflow.count("Build the offline arm64 release envelope once") == 1
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131" in workflow
    assert "github.run_attempt" in workflow
    assert "release_artifact_id: ${{ steps.release_artifact.outputs.artifact-id }}" in workflow
    assert "artifact-ids: ${{ needs.check.outputs.release_artifact_id }}" in workflow
    assert 'gh release download "v$RELEASE_VERSION"' in workflow
    assert "repos/$RELEASE_REPOSITORY/git/tags" in workflow
    assert "repos/$RELEASE_REPOSITORY/git/refs" in workflow
    assert "generate_release_notes=true" in workflow
    assert "'.immutable'" not in workflow
    assert "unauthenticated readback" in workflow
    assert "trace-marketing-managed-release:$tag:$RELEASE_SHA" in workflow
    assert "Managed trace-marketing release $tag at $RELEASE_SHA" in workflow
    assert "Remove only this run's unpublished draft state" in workflow
    assert "setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d" in workflow
    assert "attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a" in workflow
    assert "tests/fixtures/fake-gh-release.py" in workflow
    assert "Prove a fresh managed product bootstrap" in workflow
    assert "build/release/trace-marketing-bootstrap.py" in workflow
    assert 'test -L "$fresh/product/current"' in workflow
    assert "release-receipt.json" in workflow
    assert 'gh attestation verify "$asset"' in workflow
    assert "gh api --paginate --slurp" in workflow
    assert '"repos/$RELEASE_REPOSITORY/releases?per_page=100"' in workflow
    assert (
        '--signer-workflow "$RELEASE_REPOSITORY/.github/workflows/release-mac-worker.yml"'
        in workflow
    )
    assert "--source-ref refs/heads/main" in workflow
    assert '--source-digest "$RELEASE_SHA"' in workflow
    assert "--deny-self-hosted-runners" in workflow
    assert 'gh release verify "$tag"' not in workflow
    assert workflow.index("Verify the exact attested release before publication") < workflow.index(
        "Publish the verified new stable release"
    )
    assert '-f target_commitish="$RELEASE_SHA"' in workflow
    assert "SSH" not in workflow
    assert 'requires = ["hatchling==1.32.0"]' in PROJECT.read_text(encoding="utf-8")


def test_release_workflow_checks_the_reduced_worker_wheel() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "tests/marketing/test_bridge.py" not in workflow
    assert "tests/marketing/test_native_capture.py" in workflow
    assert "tests/marketing/test_worker_loop.py" in workflow
    assert "entry_points.txt" in workflow
    assert "trace-marketing = ads_booster.cli.marketing:app" in workflow
    assert "ads_booster/cli/trace_run.py" in workflow
    assert "ads_booster/assets/iphone-ui.png" in workflow


def test_cloudflare_deploy_tracks_the_actual_packaged_static_sources() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "src/ads_booster/assets/context/**" in workflow
    assert "cloudflare/static/**" in workflow
    assert "src/trace_capture/" not in workflow
    assert "TRACE_DEPLOY_SHA: ${{ github.sha }}" in workflow
    assert ".commit_sha == $sha" in workflow
    assert 'read_exact_health "https://workspace.borca.ai/health"' in workflow


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
