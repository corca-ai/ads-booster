#!/usr/bin/env python3
"""Build the deterministic metadata envelope for a Mac worker release."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import tarfile
from hashlib import sha256
from pathlib import Path
from typing import Final, cast

_SEMVER: Final = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}")
_BOOTSTRAP_NAME: Final = "trace-marketing-bootstrap.py"
_MANIFEST_NAME: Final = "trace-marketing-release.json"


def main() -> None:
    arguments = _arguments()
    version = cast("str", arguments.version)
    commit_sha = cast("str", arguments.commit_sha)
    if _SEMVER.fullmatch(version) is None:
        message = "version must use strict semantic versioning"
        raise SystemExit(message)
    if _COMMIT_SHA.fullmatch(commit_sha) is None:
        message = "commit SHA must contain exactly 40 lower-case hex characters"
        raise SystemExit(message)
    wheelhouse = cast("Path", arguments.wheelhouse).resolve(strict=True)
    requirements = cast("Path", arguments.requirements).resolve(strict=True)
    bootstrap_source = cast("Path", arguments.bootstrap).resolve(strict=True)
    output = cast("Path", arguments.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheels = tuple(sorted(wheelhouse.glob("*.whl")))
    project_wheels = tuple(wheelhouse.glob(f"trace_appium_capture-{version}-*.whl"))
    if not wheels or len(project_wheels) != 1 or any(path.is_symlink() for path in wheels):
        message = "wheelhouse must contain one exact project wheel and binary dependencies"
        raise SystemExit(message)
    if not requirements.read_text(encoding="utf-8").strip():
        message = "requirements lock must not be empty"
        raise SystemExit(message)

    bootstrap = output / _BOOTSTRAP_NAME
    _ = shutil.copyfile(bootstrap_source, bootstrap)
    bootstrap.chmod(0o755)
    bundle = output / f"trace-marketing-macos-arm64-v{version}.tar.gz"
    _write_bundle(bundle, wheels=wheels, requirements=requirements)
    manifest = {
        "schema_version": "trace.marketing-release.v1",
        "version": version,
        "tag": f"v{version}",
        "commit_sha": commit_sha,
        "platform": "macos-arm64",
        "python": "3.14",
        "package": "trace-appium-capture",
        "bundle": _file_metadata(bundle),
        "bootstrap": _file_metadata(bootstrap),
    }
    manifest_path = output / _MANIFEST_NAME
    _ = manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o644)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--version", required=True)
    _ = parser.add_argument("--commit-sha", required=True)
    _ = parser.add_argument("--wheelhouse", required=True, type=Path)
    _ = parser.add_argument("--requirements", required=True, type=Path)
    _ = parser.add_argument("--bootstrap", required=True, type=Path)
    _ = parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _write_bundle(
    path: Path,
    *,
    wheels: tuple[Path, ...],
    requirements: Path,
) -> None:
    with (
        path.open("wb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as gzip_stream,
        tarfile.open(fileobj=gzip_stream, mode="w") as archive,
    ):
        _add_directory(archive, "wheelhouse")
        for wheel in wheels:
            _add_file(archive, wheel, f"wheelhouse/{wheel.name}")
        _add_file(archive, requirements, "requirements.lock")
    path.chmod(0o644)


def _add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    archive.addfile(info)


def _add_file(archive: tarfile.TarFile, source: Path, name: str) -> None:
    payload = source.read_bytes()
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    archive.addfile(info, io.BytesIO(payload))


def _file_metadata(path: Path) -> dict[str, str | int]:
    payload = path.read_bytes()
    return {"name": path.name, "sha256": sha256(payload).hexdigest(), "size": len(payload)}


if __name__ == "__main__":
    main()
