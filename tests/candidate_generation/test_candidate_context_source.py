from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.candidate_generation import (
    REQUIRED_DOCUMENTS,
    CandidateContextMissingError,
    CandidateContextSource,
    CandidateReferencesMissingError,
    CandidateReferenceSource,
    default_context_directory,
    reference_directory,
    reference_id,
)
from tests.candidate_generation._corpus import (
    FAKE_FLOPS,
    FAKE_HITS,
    first,
    reference_body,
    write_context,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_context_directory_prefers_workspace_and_can_be_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a workspace context and an explicit alternate location
    local = write_context(tmp_path)

    # When / Then workspace context wins until the operator explicitly overrides it
    assert default_context_directory(tmp_path) == local
    monkeypatch.setenv("TRACE_AGENT_CONTEXT_DIR", str(tmp_path / "elsewhere"))
    assert default_context_directory(tmp_path) == tmp_path / "elsewhere"


def test_the_packaged_corpus_carries_every_document_generation_names(tmp_path: Path) -> None:
    """A released Mac worker has no checkout, so the corpus has to travel in the wheel."""
    # Given no workspace-owned context directory
    directory = default_context_directory(tmp_path)

    # When the packaged context is read the way generation reads it
    bundle = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
    pool = CandidateReferenceSource(directory).load("KR")

    # Then every named document is present and non-empty, and the corpus can be sampled
    assert tuple(document.relative_path for document in bundle.documents) == REQUIRED_DOCUMENTS
    assert all(document.text.strip() for document in bundle.documents)
    assert len(pool.hits) >= 3
    assert pool.flops


def test_missing_context_file_names_the_file(tmp_path: Path) -> None:
    # Given a context snapshot with two of the named documents absent
    directory = write_context(tmp_path, skip=("core/FACTS.md", "references/KR/INDEX.md"))

    # When / Then the typed failure names both unusable inputs
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
    assert failure.value.missing == ("core/FACTS.md", "references/KR/INDEX.md")
    assert "core/FACTS.md" in failure.value.message
    assert "references/KR/INDEX.md" in failure.value.message


def test_blank_context_file_counts_as_missing(tmp_path: Path) -> None:
    # Given a document that exists but says nothing
    directory = write_context(tmp_path)
    _ = (directory / "core" / "VOICE-KR.md").write_text("   \n", encoding="utf-8")

    # When / Then a blank document is as unusable as an absent one
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
    assert failure.value.missing == ("core/VOICE-KR.md",)


def test_missing_context_directory_reports_its_resolved_location(tmp_path: Path) -> None:
    # Given a configured context directory does not exist
    missing = tmp_path / "missing-context"

    # When / Then the boundary reports the exact location rather than a generic failure
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(missing).load()
    assert failure.value.directory == missing


def test_a_reference_sample_is_three_hits_and_one_flop(tmp_path: Path) -> None:
    """A batch shown only winners writes pastiche of them.

    What did not work is the half of the corpus that says where the line is, so every call
    reads one flop alongside its three hits.
    """
    # Given the reference corpus classified by the outcome in each file's frontmatter
    directory = write_context(tmp_path)
    pool = CandidateReferenceSource(directory).load("KR")

    # When one call draws its sample
    sample = pool.sample(first)

    # Then it is three hits and one flop, and the INDEX table is not among them
    assert len(pool.hits) == FAKE_HITS
    assert len(pool.flops) == FAKE_FLOPS
    assert [reference_id(document) for document in sample] == [
        "kr-900",
        "kr-901",
        "kr-902",
        "kr-904",
    ]
    assert "INDEX" not in " ".join(document.relative_path for document in sample)


def test_a_corpus_shorter_than_the_sample_gives_what_it_has(tmp_path: Path) -> None:
    """A country with two references still has to be able to generate."""
    # Given a corpus with one hit and no flops
    directory = tmp_path / "context" / "references" / "KR"
    directory.mkdir(parents=True)
    _ = (directory / "kr-900.md").write_text(reference_body("hit", 0), encoding="utf-8")

    # When a sample is drawn
    pool = CandidateReferenceSource(tmp_path / "context").load("KR")
    sample = pool.sample(first)

    # Then it is the one document rather than a failure
    assert [reference_id(document) for document in sample] == ["kr-900"]


def test_only_the_frontmatter_decides_a_reference_outcome(tmp_path: Path) -> None:
    """A reference body quotes other posts and their results; that is not its own verdict."""
    # Given a hit whose prose happens to contain a flop line
    directory = tmp_path / "context" / "references" / "KR"
    directory.mkdir(parents=True)
    _ = (directory / "kr-900.md").write_text(
        f"{reference_body('hit', 0)}\n\noutcome: flop 이라고 적힌 남의 게시물 인용",
        encoding="utf-8",
    )
    # And a file with no frontmatter at all
    _ = (directory / "INDEX.md").write_text("# INDEX\noutcome: hit", encoding="utf-8")

    # When the corpus is read
    pool = CandidateReferenceSource(tmp_path / "context").load("KR")

    # Then the frontmatter verdict wins and the table is not a reference
    assert [reference_id(document) for document in pool.hits] == ["kr-900"]
    assert pool.flops == ()


def test_the_reference_corpus_is_chosen_by_country(tmp_path: Path) -> None:
    """The folder is a parameter, not a constant, so a second country reaches this code."""
    # Given two countries' corpora side by side
    directory = write_context(tmp_path)
    japanese = directory / reference_directory("JP")
    japanese.mkdir(parents=True)
    _ = (japanese / "jp-900.md").write_text(reference_body("hit", 0), encoding="utf-8")

    # When each is read
    korean_pool = CandidateReferenceSource(directory).load("KR")
    japanese_pool = CandidateReferenceSource(directory).load("JP")

    # Then each country sees only its own posts
    assert reference_directory("KR") == "references/KR"
    assert len(korean_pool.hits) == FAKE_HITS
    assert [reference_id(document) for document in japanese_pool.hits] == ["jp-900"]


def test_a_country_without_references_fails_instead_of_borrowing_another(
    tmp_path: Path,
) -> None:
    """Silence here would ship Korean-grounded captions labelled as another country.

    The corpus is KR-only today. Falling back would produce a batch written from the wrong
    audience's posts with nothing downstream saying so, so the wall is explicit and names
    the country that hit it.
    """
    # Given a context directory with a Korean corpus and nothing else
    directory = write_context(tmp_path)

    # When a country with no references is asked for
    with pytest.raises(CandidateReferencesMissingError) as failure:
        _ = CandidateReferenceSource(directory).load("JP")

    # Then the message names the country and the path rather than failing quietly
    assert failure.value.country == "JP"
    assert "JP 레퍼런스가 없습니다" in failure.value.message
    assert "references/JP" in failure.value.message
