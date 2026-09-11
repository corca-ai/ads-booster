"""Restricted one-turn Codex boundary for the installed Trace post workflow."""
# ruff: noqa: EM101

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ads_booster.providers.codex_image_edit import (
    ImageEditProcessRequest,
    ImageEditRunner,
    SubprocessAppServerImageEditRunner,
)

_MIN_IMAGE_GENERATIONS = 7
_MAX_IMAGE_GENERATIONS = 14


@dataclass(frozen=True, slots=True)
class TracePostProviderResult:
    thread_id: str
    turn_id: str
    images: tuple[TracePostGeneratedImage, ...]


@dataclass(frozen=True, slots=True)
class TracePostGeneratedImage:
    event_id: str
    path: Path
    sha256: str


@dataclass(slots=True)
class CodexTracePostProvider:
    executable: Path
    model: str
    runner: ImageEditRunner = field(default_factory=SubprocessAppServerImageEditRunner)

    def run(
        self, *, workspace: Path, instruction: str, timeout_seconds: float
    ) -> TracePostProviderResult:
        executable = self.executable.resolve(strict=True)
        root = workspace.resolve(strict=True)
        python_root = Path(sys.base_prefix).resolve(strict=True)
        response = self.runner.run(
            ImageEditProcessRequest(
                executable=executable,
                model=self.model,
                workspace=root,
                prompt=instruction,
                image_paths=(),
                timeout_seconds=timeout_seconds,
                permission_profile="trace-post-restricted",
                allow_shell=True,
                read_paths=(python_root,),
                base_instructions=(
                    "Run the installed Trace post workflow exactly once using only the frozen "
                    "workspace bundle, local shell, and official image generation."
                ),
                developer_instructions=(
                    "Use the exact Python interpreter named in the prompt. Never access the "
                    "network, remote tools, user configuration, or paths outside cwd except "
                    "that interpreter runtime. After each imageGeneration completes, ignore "
                    "its savedPath and pass ./provider-images/<image item id>.png to the frozen "
                    "image_call.py complete command. Wait for that file if needed. Do not "
                    "exceed fourteen image generations."
                ),
                allowed_item_types=(
                    "imageGeneration",
                    "agentMessage",
                    "reasoning",
                    "userMessage",
                    "commandExecution",
                    "imageView",
                    "fileChange",
                    "plan",
                    "contextCompaction",
                ),
                min_image_generations=_MIN_IMAGE_GENERATIONS,
                max_image_generations=_MAX_IMAGE_GENERATIONS,
                reasoning_effort="medium",
                materialize_image_results=True,
                max_stream_bytes=256 * 1024 * 1024,
            )
        )
        if not _MIN_IMAGE_GENERATIONS <= len(response.items) <= _MAX_IMAGE_GENERATIONS:
            raise RuntimeError("codex_trace_post_outcome_unknown")
        images: list[TracePostGeneratedImage] = []
        paths: set[Path] = set()
        for item in response.items:
            saved_path = item.get("savedPath")
            event_id = item.get("id")
            native_sha256 = item.get("materializedSha256")
            if (
                item.get("type") != "imageGeneration"
                or item.get("status") != "completed"
                or item.get("failure") is not None
                or not isinstance(saved_path, str)
                or not isinstance(event_id, str)
                or not event_id
                or not isinstance(native_sha256, str)
            ):
                raise RuntimeError("codex_trace_post_outcome_unknown")
            output = Path(saved_path)
            expected_directory = root / "provider-images"
            if (
                not output.is_absolute()
                or output.is_symlink()
                or expected_directory.is_symlink()
                or output.parent != expected_directory
                or output.name != f"{event_id}.png"
            ):
                raise RuntimeError("codex_trace_post_outcome_unknown")
            resolved = output.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file() or resolved in paths:
                raise RuntimeError("codex_trace_post_outcome_unknown")
            digest = _sha256(resolved)
            if digest != native_sha256:
                raise RuntimeError("codex_trace_post_outcome_unknown")
            paths.add(resolved)
            images.append(TracePostGeneratedImage(event_id, resolved, native_sha256))
        return TracePostProviderResult(
            thread_id=response.thread_id,
            turn_id=response.turn_id,
            images=tuple(images),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["CodexTracePostProvider", "TracePostGeneratedImage", "TracePostProviderResult"]
