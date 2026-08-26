from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from trace_capture.candidate_generation.errors import CandidateContextMissingError
from trace_capture.candidate_generation.models import (
    CandidateContextBundle,
    CandidateDocument,
    CandidateReferenceBody,
)
from trace_capture.candidate_generation.parsing import REFERENCE_ID_PATTERN

if TYPE_CHECKING:
    from collections.abc import Sequence

CONTEXT_DIR_ENVIRONMENT: Final = "TRACE_AGENT_CONTEXT_DIR"
CONTEXT_DIRECTORY_NAME: Final = "context"
REFERENCE_DIRECTORY: Final = "references/KR"
REFERENCE_INDEX_PATH: Final = f"{REFERENCE_DIRECTORY}/INDEX.md"
REQUIRED_DOCUMENTS: Final = (
    "core/PRINCIPLES-GLOBAL.md",
    "core/PRINCIPLES-KR.md",
    "core/ELEMENTS-KR.md",
    "core/VOICE-KR.md",
    "core/FACTS.md",
    REFERENCE_INDEX_PATH,
)
MAX_REFERENCE_BODIES: Final = 8
MAX_REFERENCE_CHARS: Final = 50_000


def default_context_directory(workspace: Path) -> Path:
    """Resolve the context directory from the environment or the serve workspace."""
    configured = os.environ.get(CONTEXT_DIR_ENVIRONMENT)
    if configured is not None:
        return Path(configured).expanduser()
    return workspace / CONTEXT_DIRECTORY_NAME


@dataclass(frozen=True, slots=True)
class CandidateContextSource:
    """Reads the Korean context documents and reference bodies the generation call assembles."""

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

    def load_references(self, reference_ids: Sequence[str]) -> tuple[CandidateReferenceBody, ...]:
        """Read the full text of the selected references, in the order they were selected.

        Every path is built from the id alone and re-checked against the reference directory,
        so a selected id can never reach a file outside `references/KR/`. An id that does not
        resolve to a readable file is dropped; once the accumulated text would pass
        `MAX_REFERENCE_CHARS`, the remaining references are dropped from the end.
        """
        root = (self.directory / REFERENCE_DIRECTORY).resolve()
        bodies: list[CandidateReferenceBody] = []
        remaining = MAX_REFERENCE_CHARS
        for reference_id in reference_ids[:MAX_REFERENCE_BODIES]:
            text = _read_reference(root, reference_id)
            if text is None:
                continue
            if len(text) > remaining:
                break
            remaining -= len(text)
            bodies.append(CandidateReferenceBody(reference_id=reference_id, text=text))
        return tuple(bodies)


def _read_reference(root: Path, reference_id: str) -> str | None:
    """Return one reference body, or None when the id is unusable or the file is absent."""
    if REFERENCE_ID_PATTERN.fullmatch(reference_id) is None:
        return None
    path = (root / f"{reference_id}.md").resolve()
    if path.parent != root:
        return None
    return _read(path)


def _read(path: Path) -> str | None:
    """Return the document text, or None when it is absent, unreadable, or blank."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    return text if text.strip() else None
