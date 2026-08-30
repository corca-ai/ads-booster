"""A fake context corpus, small enough to assert against and shaped like the real one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ads_booster.candidate_generation import REQUIRED_DOCUMENTS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.candidate_generation import CandidateDocument

# Enough hits and flops for one 3-hit, 1-flop sample.
FAKE_HITS: Final = 4
FAKE_FLOPS: Final = 2


def reference_body(outcome: str, index: int) -> str:
    """One reference body shaped like the corpus: frontmatter first, verdict inside it."""
    return (
        f"---\nid: kr-9{index:02d}\ncountry: KR\noutcome: {outcome}\n"
        f"relative: 1.0\n---\n\n# kr-9{index:02d} {outcome} 본문"
    )


def write_context(root: Path, *, skip: Sequence[str] = ()) -> Path:
    """Lay down every required document plus a Korean reference corpus."""
    directory = root / "context"
    for relative_path in REQUIRED_DOCUMENTS:
        if relative_path in skip:
            continue
        path = directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(f"# {relative_path}\n내용", encoding="utf-8")
    references = directory / "references" / "KR"
    references.mkdir(parents=True, exist_ok=True)
    for index in range(FAKE_HITS):
        _ = (references / f"kr-9{index:02d}.md").write_text(
            reference_body("hit", index), encoding="utf-8"
        )
    for index in range(FAKE_HITS, FAKE_HITS + FAKE_FLOPS):
        _ = (references / f"kr-9{index:02d}.md").write_text(
            reference_body("flop", index), encoding="utf-8"
        )
    return directory


def first(population: Sequence[CandidateDocument], count: int) -> Sequence[CandidateDocument]:
    """A deterministic stand-in for random.sample, so a test can name the sample it expects."""
    return list(population)[:count]
