from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trace_capture.candidate_generation.errors import CandidateContextMissingError
from trace_capture.candidate_generation.models import CandidateContextBundle, CandidateDocument

CONTEXT_DIR_ENVIRONMENT: Final = "TRACE_AGENT_CONTEXT_DIR"
CONTEXT_DIRECTORY_NAME: Final = "context"
REQUIRED_DOCUMENTS: Final = (
    "core/PRINCIPLES-GLOBAL.md",
    "core/PRINCIPLES-KR.md",
    "core/ELEMENTS-KR.md",
    "core/VOICE-KR.md",
    "core/FACTS.md",
    "references/KR/INDEX.md",
)


def default_context_directory(workspace: Path) -> Path:
    """Resolve the context directory from the environment or the serve workspace."""
    configured = os.environ.get(CONTEXT_DIR_ENVIRONMENT)
    if configured is not None:
        return Path(configured).expanduser()
    return workspace / CONTEXT_DIRECTORY_NAME


@dataclass(frozen=True, slots=True)
class CandidateContextSource:
    """Reads the fixed set of Korean context documents the generation call assembles."""

    directory: Path
    required: tuple[str, ...] = REQUIRED_DOCUMENTS

    def load(self) -> CandidateContextBundle:
        if not self.directory.is_dir():
            raise CandidateContextMissingError(self.directory)
        documents: list[CandidateDocument] = []
        missing: list[str] = []
        for relative_path in self.required:
            text = _read(self.directory / relative_path)
            if text is None:
                missing.append(relative_path)
                continue
            documents.append(CandidateDocument(relative_path=relative_path, text=text))
        if missing:
            raise CandidateContextMissingError(self.directory, tuple(missing))
        return CandidateContextBundle(
            directory=str(self.directory),
            documents=tuple(documents),
        )


def _read(path: Path) -> str | None:
    """Return the document text, or None when it is absent, unreadable, or blank."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    return text if text.strip() else None
