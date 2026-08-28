from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from ads_booster.candidate_generation.errors import (
    CandidateContextMissingError,
    CandidateReferencesMissingError,
)
from ads_booster.candidate_generation.models import CandidateContextBundle, CandidateDocument
from ads_booster.default_assets import default_candidate_context_path

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CONTEXT_DIR_ENVIRONMENT: Final = "TRACE_AGENT_CONTEXT_DIR"
CONTEXT_DIRECTORY_NAME: Final = "context"
_MARKDOWN_PATTERN: Final = "*.md"
_READ_ERRORS: Final = (OSError, UnicodeDecodeError)
# The documents the single-call script engine assembles, in the order it sends them. The
# whole corpus cannot go into one instruction — the KR reference folder alone is dozens of
# files — so that engine names the six it reasons from and fails loudly if one is absent.
# The tool-loop connector leaves this empty and discovers every readable document instead.
REQUIRED_DOCUMENTS: Final = (
    "core/PRINCIPLES-GLOBAL.md",
    "core/PRINCIPLES-KR.md",
    "core/ELEMENTS-KR.md",
    "core/VOICE-KR.md",
    "core/FACTS.md",
    "references/KR/INDEX.md",
)


def default_context_directory(workspace: Path) -> Path:
    """Resolve an explicit, workspace-owned, or packaged candidate context."""
    configured = os.environ.get(CONTEXT_DIR_ENVIRONMENT)
    if configured is not None:
        return Path(configured).expanduser()
    local = workspace / CONTEXT_DIRECTORY_NAME
    return local if local.is_dir() else default_candidate_context_path()


@dataclass(frozen=True, slots=True)
class CandidateContextSource:
    """Reads the workspace's marketing context documents.

    With `required` set, exactly those relative paths are read, in that order, and any one
    of them missing fails the load. With `required` empty, every readable markdown document
    under the directory is discovered instead.
    """

    directory: Path
    required: tuple[str, ...] = ()

    def load(self) -> CandidateContextBundle:
        if not self.directory.is_dir():
            raise CandidateContextMissingError(self.directory)
        documents: list[CandidateDocument] = []
        unreadable: list[str] = []
        paths = (
            [self.directory / relative_path for relative_path in self.required]
            if self.required
            else sorted(self.directory.rglob(_MARKDOWN_PATTERN))
        )
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


REFERENCE_ROOT: Final = "references"
_HIT_SAMPLE: Final = 3
_FLOP_SAMPLE: Final = 1
_OUTCOME_HIT: Final = "hit"
_OUTCOME_FLOP: Final = "flop"
_FRONTMATTER_FENCE: Final = "---"
_OUTCOME_FIELD: Final = "outcome:"


@dataclass(frozen=True, slots=True)
class ReferencePool:
    """Every reference body this country has, split by the outcome its frontmatter records.

    Read once per batch and sampled per call. The corpus is 41 files: sending all of them
    would dominate the instruction, and sending none — which is what happened until now —
    left the model citing reference ids from an INDEX table it had never seen the writing
    behind.
    """

    hits: tuple[CandidateDocument, ...]
    flops: tuple[CandidateDocument, ...]

    def sample(
        self,
        choose: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]],
        *,
        hits: int = _HIT_SAMPLE,
        flops: int = _FLOP_SAMPLE,
    ) -> tuple[CandidateDocument, ...]:
        """Draw the reference bodies one generation call reads.

        Flops are drawn alongside hits on purpose: what did not work is the half of the
        corpus that says where the line is, and a batch shown only winners writes pastiche
        of them. Asking for more than the pool holds takes the pool rather than failing —
        a country with two flops still has to be able to generate.
        """
        drawn = [
            *choose(self.hits, min(hits, len(self.hits))),
            *choose(self.flops, min(flops, len(self.flops))),
        ]
        return tuple(drawn)


def reference_id(document: CandidateDocument) -> str:
    """The corpus id of one reference body, which is its filename without the suffix."""
    return PurePosixPath(document.relative_path).stem


def reference_directory(country: str) -> str:
    """Where one country's reference bodies live, relative to the context directory."""
    return f"{REFERENCE_ROOT}/{country}"


@dataclass(frozen=True, slots=True)
class CandidateReferenceSource:
    """Loads one country's reference bodies, classified by the outcome each records."""

    directory: Path
    root: str = REFERENCE_ROOT

    def load(self, country: str) -> ReferencePool:
        """Read the corpus for `country`, or say plainly that there is not one.

        The country comes from the account being written as, not from a constant, so a
        second country reaches this code the moment one is created. There is no fallback:
        writing a JP account from the Korean corpus would produce captions grounded in the
        wrong audience with nothing downstream saying so, which is worse than refusing.
        """
        root = self.directory / self.root / country
        if not root.is_dir():
            raise CandidateReferencesMissingError(root, country)
        hits: list[CandidateDocument] = []
        flops: list[CandidateDocument] = []
        for path in sorted(root.glob(_MARKDOWN_PATTERN)):
            text = None if path.is_symlink() or not path.is_file() else _read(path)
            if text is None:
                continue
            document = CandidateDocument(
                relative_path=path.relative_to(self.directory).as_posix(),
                text=text,
            )
            outcome = _outcome(text)
            if outcome == _OUTCOME_HIT:
                hits.append(document)
            elif outcome == _OUTCOME_FLOP:
                flops.append(document)
        return ReferencePool(hits=tuple(hits), flops=tuple(flops))


def _outcome(text: str) -> str | None:
    """Read `outcome` out of the leading frontmatter block, or None when there is none.

    Only the frontmatter is scanned: the body of a reference quotes other posts and their
    results, and a line in the prose is not this document's own verdict.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    for line in lines[1:]:
        if line.strip() == _FRONTMATTER_FENCE:
            return None
        if line.startswith(_OUTCOME_FIELD):
            return line[len(_OUTCOME_FIELD) :].strip() or None
    return None
