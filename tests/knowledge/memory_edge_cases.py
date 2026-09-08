from __future__ import annotations

import pytest

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contracts import DependencyState, MemoryEntryKind, MemoryOrigin
from ads_booster.knowledge.memory import (
    MemorySnapshot,
    WikiClaimRecord,
    invalidate_wiki_dependencies,
    visible_memory_entries,
)
from tests.knowledge.change_test_fixtures import (
    claim,
    digest,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    wiki_summary_ref,
)


def test_wiki_title_only_change_keeps_summary_current() -> None:
    # Given
    original_claim = claim()
    document = memory_document()
    summary = memory_entry(
        document=document,
        origin=MemoryOrigin.WIKI_SUMMARY,
        kind=MemoryEntryKind.FACT,
        wiki_ref=wiki_summary_ref(source_claim=original_claim),
    ).model_copy(update={"authority_ref": None})

    # When
    entries = invalidate_wiki_dependencies(
        entries=(summary,),
        current_claims=(
            WikiClaimRecord(
                page_id="page.pricing",
                revision_id="page.pricing.r2",
                claim=original_claim,
            ),
        ),
    )

    # Then
    assert entries[0].dependency_state is DependencyState.CURRENT


def test_restricted_upstream_hides_summary_without_hiding_direct_entry() -> None:
    # Given
    original_claim = claim()
    document = memory_document()
    direct = memory_entry(document=document, entry_id="entry.direct")
    summary = memory_entry(
        document=document,
        entry_id="entry.summary",
        origin=MemoryOrigin.WIKI_SUMMARY,
        kind=MemoryEntryKind.FACT,
        wiki_ref=wiki_summary_ref(source_claim=original_claim),
    ).model_copy(update={"authority_ref": None})

    # When
    entries = invalidate_wiki_dependencies(
        entries=(direct, summary),
        current_claims=(),
        restricted_claim_ids=frozenset((original_claim.claim_id,)),
    )

    # Then
    assert entries[0].dependency_state is DependencyState.CURRENT
    assert entries[1].dependency_state is DependencyState.RESTRICTED


def test_valid_empty_memory_is_distinct_from_corrupt_body() -> None:
    # Given
    document, revision, _, _ = memory_snapshot_parts(entries=())
    empty_revision = revision.model_copy(update={"body_sha256": digest(""), "entry_ids": ()})
    empty = MemorySnapshot(document, empty_revision, (), b"")

    # When / Then
    assert visible_memory_entries(empty) == ()
    with pytest.raises(ChangeValidationError, match="memory_body_digest_mismatch"):
        _ = MemorySnapshot(document, empty_revision, (), b"corrupt")
