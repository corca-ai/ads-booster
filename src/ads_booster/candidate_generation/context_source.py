from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ads_booster.candidate_generation.errors import CandidateContextMissingError
from ads_booster.candidate_generation.models import CandidateContextBundle, CandidateDocument
from ads_booster.default_assets import default_candidate_context_path

CONTEXT_DIR_ENVIRONMENT: Final = "TRACE_AGENT_CONTEXT_DIR"
CONTEXT_DIRECTORY_NAME: Final = "context"
_MARKDOWN_PATTERN: Final = "*.md"
_READ_ERRORS: Final = (OSError, UnicodeDecodeError)


def default_context_directory(workspace: Path) -> Path:
    """Resolve an explicit, workspace-owned, or packaged candidate context."""
    configured = os.environ.get(CONTEXT_DIR_ENVIRONMENT)
    if configured is not None:
        return Path(configured).expanduser()
    local = workspace / CONTEXT_DIRECTORY_NAME
    return local if local.is_dir() else default_candidate_context_path()


@dataclass(frozen=True, slots=True)
class CandidateContextSource:
    """Discovers the workspace's readable marketing context documents."""

    directory: Path

    def load(self) -> CandidateContextBundle:
        if not self.directory.is_dir():
            raise CandidateContextMissingError(self.directory)
        documents: list[CandidateDocument] = []
        unreadable: list[str] = []
        paths = sorted(self.directory.rglob(_MARKDOWN_PATTERN))
        for path in paths:
            relative_path = path.relative_to(self.directory).as_posix()
            text = None if path.is_symlink() or not path.is_file() else _read(path)
            if text is None:
                unreadable.append(relative_path)
                continue
            documents.append(CandidateDocument(relative_path=relative_path, text=text))
        if unreadable or not documents:
            missing = tuple(unreadable) or (_MARKDOWN_PATTERN,)
            raise CandidateContextMissingError(self.directory, missing)
        return CandidateContextBundle(
            directory=str(self.directory),
            documents=tuple(documents),
        )


def _read(path: Path) -> str | None:
    """Return the document text, or None when it is absent, unreadable, or blank."""
    try:
        text = path.read_text(encoding="utf-8")
    except _READ_ERRORS:
        return None
    return text if text.strip() else None
