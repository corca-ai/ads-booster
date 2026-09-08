from __future__ import annotations

import pytest

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contracts import PageRelation, PageRelationKind, WikiPageStatus
from ads_booster.knowledge.pages import (
    PageSnapshot,
    link_page,
    merge_pages,
    page_candidates,
    rename_page,
    split_page,
    unlink_page,
)
from tests.knowledge.change_test_fixtures import WORKSPACE_SCOPE, claim, page_snapshot


def test_rename_preserves_page_identity_claims_and_old_title_alias() -> None:
    # Given
    page, revision, body = page_snapshot()

    # When
    renamed = rename_page(
        PageSnapshot(page=page, revision=revision, body=body),
        title="Launch pricing",
        revision_id="page.pricing.r2",
    )

    # Then
    assert renamed.page.page_id == page.page_id
    assert renamed.page.aliases == ("Pricing",)
    assert renamed.revision.claims == revision.claims
    assert renamed.revision.previous_revision_id == revision.revision_id


def test_unlink_removes_only_current_relation() -> None:
    # Given
    page, revision, body = page_snapshot()
    relation = PageRelation(
        relation_id="relation.related",
        from_page_id=page.page_id,
        to_page_id="page.other",
        kind=PageRelationKind.RELATED_TO,
        reason="Useful neighboring topic.",
    )
    snapshot = PageSnapshot(
        page=page,
        revision=revision.model_copy(update={"relations": (relation,)}),
        body=body,
    )

    # When
    unlinked = unlink_page(
        snapshot, relation_id=relation.relation_id, revision_id="page.pricing.r2"
    )

    # Then
    assert unlinked.revision.relations == ()
    assert snapshot.revision.relations == (relation,)


def test_merge_preserves_stable_claim_ids_and_redirects_sources() -> None:
    # Given
    target = PageSnapshot(*page_snapshot())
    source_page, source_revision, source_body = page_snapshot(
        page_id="page.price-history",
        title="Price history",
        revision_id="page.price-history.r1",
        claims=(claim(claim_id="claim.history", statement="The earlier price was 19 dollars."),),
    )
    source = PageSnapshot(source_page, source_revision, source_body)

    # When
    merged = merge_pages(
        target=target,
        sources=(source,),
        claim_target_map={"claim.history": target.page.page_id},
        revision_ids={
            target.page.page_id: "page.pricing.r2",
            source.page.page_id: "page.price-history.r2",
        },
        reason="Both pages describe the same pricing history.",
    )

    # Then
    assert {item.claim_id for item in merged.current[target.page.page_id].revision.claims} == {
        "claim.primary",
        "claim.history",
    }
    assert merged.current[source.page.page_id].page.status is WikiPageStatus.REDIRECT
    assert merged.redirects[source.page.page_id] == target.page.page_id


def test_merge_rejects_incomplete_claim_mapping_without_partial_result() -> None:
    # Given
    target = PageSnapshot(*page_snapshot())
    source = PageSnapshot(
        *page_snapshot(
            page_id="page.other",
            revision_id="page.other.r1",
            claims=(claim(claim_id="claim.unmapped"),),
        )
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="claim_mapping_incomplete"):
        _ = merge_pages(
            target=target,
            sources=(source,),
            claim_target_map={},
            revision_ids={
                target.page.page_id: "page.pricing.r2",
                source.page.page_id: "page.other.r2",
            },
            reason="Duplicate topic.",
        )


def test_split_requires_exhaustive_claim_mapping_and_preserves_original_as_overview() -> None:
    # Given
    original_page, original_revision, body = page_snapshot(
        claims=(claim(), claim(claim_id="claim.history"))
    )
    original = PageSnapshot(original_page, original_revision, body)

    # When
    result = split_page(
        source=original,
        destinations={
            "page.current-price": ("Current price", WORKSPACE_SCOPE),
            "page.price-history": ("Price history", WORKSPACE_SCOPE),
        },
        claim_target_map={
            "claim.primary": "page.current-price",
            "claim.history": "page.price-history",
        },
        revision_ids={
            original.page.page_id: "page.pricing.r2",
            "page.current-price": "page.current-price.r1",
            "page.price-history": "page.price-history.r1",
        },
        reason="Separate current and historical prices.",
    )

    # Then
    assert result.current[original.page.page_id].page.status is WikiPageStatus.SPLIT
    assert result.current[original.page.page_id].revision.claims == ()
    assert {
        relation.to_page_id for relation in result.current[original.page.page_id].revision.relations
    } == {
        "page.current-price",
        "page.price-history",
    }


def test_related_links_allow_bidirectional_cycle() -> None:
    # Given
    first = PageSnapshot(*page_snapshot(page_id="page.a", revision_id="page.a.r1"))
    second = PageSnapshot(*page_snapshot(page_id="page.b", revision_id="page.b.r1"))
    first_relation = PageRelation(
        relation_id="relation.a.b",
        from_page_id="page.a",
        to_page_id="page.b",
        kind=PageRelationKind.RELATED_TO,
        reason="The pages inform one another.",
    )
    second_relation = first_relation.model_copy(
        update={"relation_id": "relation.b.a", "from_page_id": "page.b", "to_page_id": "page.a"}
    )

    # When
    linked_first = link_page(first, relation=first_relation, revision_id="page.a.r2")
    linked_second = link_page(second, relation=second_relation, revision_id="page.b.r2")

    # Then
    assert linked_first.revision.relations == (first_relation,)
    assert linked_second.revision.relations == (second_relation,)


def test_ambiguous_alias_returns_all_candidates_without_automatic_merge() -> None:
    # Given
    first_page, first_revision, first_body = page_snapshot(
        page_id="page.a", revision_id="page.a.r1"
    )
    second_page, second_revision, second_body = page_snapshot(
        page_id="page.b", revision_id="page.b.r1"
    )
    first = PageSnapshot(
        first_page.model_copy(update={"aliases": ("Offer",)}),
        first_revision.model_copy(update={"aliases": ("Offer",)}),
        first_body,
    )
    second = PageSnapshot(
        second_page.model_copy(update={"aliases": ("Offer",)}),
        second_revision.model_copy(update={"aliases": ("Offer",)}),
        second_body,
    )

    # When
    candidates = page_candidates((first, second), name="offer")

    # Then
    assert candidates == ("page.a", "page.b")
