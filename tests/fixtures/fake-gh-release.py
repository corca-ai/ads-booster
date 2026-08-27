#!/usr/bin/env python3
"""Offline GitHub CLI fixture for the managed-bootstrap CI proof."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def _manifest() -> tuple[Path, dict[str, object]]:
    release_dir = Path(os.environ["TRACE_TEST_RELEASE_DIR"]).resolve()
    manifest_path = release_dir / "trace-marketing-release.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        message = "fixture manifest is not an object"
        raise SystemExit(message)
    return release_dir, payload


def _file_record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "name": path.name,
        "size": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "browser_download_url": f"https://fixtures.invalid/{path.name}",
    }


def _attestation_verify(
    arguments: list[str],
    release_dir: Path,
    manifest: dict[str, object],
) -> None:
    if arguments == ["--help"]:
        sys.stdout.write(
            "--deny-self-hosted-runners --signer-workflow --source-digest --source-ref\n"
        )
        return
    if not arguments:
        message = "fixture attestation path is missing"
        raise SystemExit(message)
    candidate = Path(arguments[0]).resolve()
    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("name"), str):
        message = "fixture bundle metadata is invalid"
        raise SystemExit(message)
    expected_names = {
        "trace-marketing-release.json",
        bundle["name"],
        "trace-marketing-bootstrap.py",
    }
    if candidate.parent != release_dir or candidate.name not in expected_names:
        message = "fixture refused an unexpected attestation subject"
        raise SystemExit(message)
    expected_flags = {
        "--repo": "corca-ai/ads-booster",
        "--signer-workflow": ("corca-ai/ads-booster/.github/workflows/release-mac-worker.yml"),
        "--source-ref": "refs/heads/main",
        "--source-digest": str(manifest["commit_sha"]),
    }
    for flag, expected in expected_flags.items():
        try:
            actual = arguments[arguments.index(flag) + 1]
        except (ValueError, IndexError) as error:
            message = f"fixture missing {flag}"
            raise SystemExit(message) from error
        if actual != expected:
            message = f"fixture rejected {flag}"
            raise SystemExit(message)
    if "--deny-self-hosted-runners" not in arguments:
        message = "fixture requires GitHub-hosted provenance"
        raise SystemExit(message)


def _api(arguments: list[str], release_dir: Path, manifest: dict[str, object]) -> None:
    endpoint = arguments[-1]
    tag = str(manifest["tag"])
    commit_sha = str(manifest["commit_sha"])
    if endpoint.endswith(f"/releases/tags/{tag}"):
        assets = [_file_record(path) for path in sorted(release_dir.iterdir()) if path.is_file()]
        payload = {
            "tag_name": tag,
            "target_commitish": commit_sha,
            "draft": False,
            "prerelease": False,
            "immutable": False,
            "assets": assets,
        }
    elif endpoint.endswith(f"/git/ref/tags/{tag}"):
        payload = {"object": {"type": "tag", "sha": "b" * 40}}
    elif endpoint.endswith(f"/git/tags/{'b' * 40}"):
        payload = {"object": {"type": "commit", "sha": commit_sha}}
    else:
        message = f"fixture refused unexpected API endpoint: {endpoint}"
        raise SystemExit(message)
    sys.stdout.write(json.dumps(payload) + "\n")


def main() -> None:
    release_dir, manifest = _manifest()
    arguments = sys.argv[1:]
    if arguments[:2] == ["attestation", "verify"]:
        _attestation_verify(arguments[2:], release_dir, manifest)
        return
    if arguments and arguments[0] == "api":
        _api(arguments[1:], release_dir, manifest)
        return
    message = "fixture supports only attestation verify and api"
    raise SystemExit(message)


if __name__ == "__main__":
    main()
