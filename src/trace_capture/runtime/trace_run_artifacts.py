from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from trace_capture.contracts.errors import ArtifactIntegrityError

if TYPE_CHECKING:
    from trace_capture.contracts import MarketingCompositeJob
    from trace_capture.contracts.run import TraceRunRequest


def prepare_artifact(path: Path | None) -> tuple[str | None, str | None]:
    if path is None:
        return None, None
    try:
        resolved = path.resolve()
    except OSError as error:
        raise ArtifactIntegrityError(
            path=str(path), reason="artifact path could not be resolved"
        ) from error
    if path.is_symlink() or not resolved.is_file():
        raise ArtifactIntegrityError(path=str(path), reason="artifact must be a regular file")
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as error:
        raise ArtifactIntegrityError(path=str(path), reason="artifact could not be read") from error
    return str(resolved), digest


def request_digest(request: TraceRunRequest) -> str:
    serialized = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def same_path(path: str, expected: Path) -> bool:
    try:
        return Path(path).resolve() == expected
    except OSError:
        return False


def contains_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current == current.parent:
            return False
        if current.is_symlink():
            return True
        current = current.parent
    return False


def safe_capture_root(output_root: Path, run_id: str) -> Path | None:
    try:
        if output_root.is_symlink():
            return None
        root = output_root.resolve()
        run_root = root / run_id
        if not run_root.resolve().is_relative_to(root) or run_root.is_symlink():
            return None
    except OSError:
        return None
    return run_root


def safe_artifact(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if path.is_symlink() or not resolved.is_file():
        return None
    return resolved


def safe_job_path(root: Path, relative_path: str) -> Path | None:
    try:
        resolved_root = root.resolve()
        candidate = root / relative_path
    except OSError:
        return None
    return safe_candidate(resolved_root, candidate, root)


def safe_candidate(root: Path, candidate: Path, stop: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root) or contains_symlink(candidate, stop):
        return None
    return resolved


def composition_paths_are_safe(job: MarketingCompositeJob, root: Path) -> bool:
    source_paths = (
        job.layers.background,
        job.layers.trace_components,
        job.layers.iphone_ui,
    )
    if any(safe_job_path(root, relative_path) is None for relative_path in source_paths):
        return False
    output = safe_job_path(root, job.output_image)
    if output is None:
        return False
    normalized = safe_candidate(
        root.resolve(), output.with_name(f"{job.job_id}-iphone-ui.png"), root.resolve()
    )
    return normalized is not None


def artifact_matches(path: Path, expected_digest: str) -> bool:
    artifact = safe_artifact(path)
    if artifact is None:
        return False
    try:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == expected_digest


__all__ = [
    "artifact_matches",
    "composition_paths_are_safe",
    "contains_symlink",
    "prepare_artifact",
    "request_digest",
    "safe_artifact",
    "safe_candidate",
    "safe_capture_root",
    "safe_job_path",
    "same_path",
]
