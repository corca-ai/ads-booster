from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ads_booster.knowledge.change_validation import ChangeValidationError, EvidenceRecord
from ads_booster.knowledge.contracts import (
    DependencyState,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    InstructionAuthority,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    Provenance,
    SoulSection,
)
from ads_booster.knowledge.memory import (
    MemorySnapshot,
    ValidationCatalog,
    WikiClaimRecord,
    handover_direct_entry_to_wiki,
    invalidate_wiki_dependencies,
    validate_memory_change,
    visible_memory_entries,
)
from tests.knowledge.change_test_fixtures import (
    NOW,
    PRIVATE_SCOPE,
    WORKSPACE_SCOPE,
    actor,
    authenticated_event,
    claim,
    digest,
    evidence,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    wiki_summary_ref,
)
from tests.knowledge.memory_edge_cases import (
    test_restricted_upstream_hides_summary_without_hiding_direct_entry,
    test_valid_empty_memory_is_distinct_from_corrupt_body,
    test_wiki_title_only_change_keeps_summary_current,
)

__all__ = [
    "test_restricted_upstream_hides_summary_without_hiding_direct_entry",
    "test_valid_empty_memory_is_distinct_from_corrupt_body",
    "test_wiki_title_only_change_keeps_summary_current",
]


def _catalog(
    *, event_capabilities: tuple[GrantCapability, ...] = (GrantCapability.WRITE,)
) -> ValidationCatalog:
    ref = evidence()
    return ValidationCatalog(
        evidence=(EvidenceRecord(ref=ref, quote="We adopt this rule."),),
        events=(authenticated_event(capabilities=event_capabilities),),
        wiki_claims=(),
    )


@pytest.mark.parametrize(
    ("kind", "document_id", "local_date"),
    [
        (MemoryKind.TEAM, "memory.team", None),
        (MemoryKind.CORE, "memory.core", None),
        (MemoryKind.DAILY, "memory.daily.2026-09-07", date(2026, 9, 7)),
    ],
)
def test_direct_memory_kinds_validate_actual_event_and_publish_new_revision(
    kind: MemoryKind,
    document_id: str,
    local_date: date | None,
) -> None:
    # Given
    document = memory_document(
        kind=kind,
        document_id=document_id,
        local_date=local_date,
    )
    entry = memory_entry(document=document)
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))

    # When
    changed = validate_memory_change(
        snapshot=snapshot,
        entries=(entry.model_copy(update={"text": "Report blockers before progress."}),),
        body=b"# Team\nReport blockers before progress.\n",
        revision_id=f"{document_id}.r2",
        actor=actor(),
        catalog=_catalog(),
        at=NOW,
    )

    # Then
    assert changed.document.head_revision_id == f"{document_id}.r2"
    assert changed.revision.previous_revision_id == f"{document_id}.r1"
    assert changed.entries[0].text == "Report blockers before progress."


def test_decision_rejects_unresolved_authority_pointer() -> None:
    # Given
    document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    entry = memory_entry(document=document)
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))
    untrusted_catalog = replace(_catalog(), events=())

    # When / Then
    with pytest.raises(ChangeValidationError, match="decision_requires_human_direct_event"):
        _ = validate_memory_change(
            snapshot=snapshot,
            entries=(entry,),
            body=b"# Team\n",
            revision_id="memory.team.r2",
            actor=actor(),
            catalog=untrusted_catalog,
            at=NOW,
        )


def test_shared_memory_rejects_private_lineage() -> None:
    # Given
    document = memory_document()
    entry = memory_entry(document=document, scope=WORKSPACE_SCOPE).model_copy(
        update={"source_refs": (evidence(scope=PRIVATE_SCOPE),)}
    )
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))
    private_ref = evidence(scope=PRIVATE_SCOPE)
    catalog = ValidationCatalog(
        evidence=(EvidenceRecord(ref=private_ref, quote="We adopt this rule."),),
        events=(authenticated_event(scope=PRIVATE_SCOPE),),
        wiki_claims=(),
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="scope_expansion_forbidden"):
        _ = validate_memory_change(
            snapshot=snapshot,
            entries=(entry,),
            body=b"# Memory\n",
            revision_id="memory.core.r2",
            actor=actor(),
            catalog=catalog,
            at=NOW,
        )


def test_wiki_summary_requires_current_semantic_fingerprint() -> None:
    # Given
    source_claim = claim()
    document = memory_document()
    claim_ref = EvidenceRef(
        evidence_kind=EvidenceKind.CLAIM,
        evidence_id=source_claim.claim_id,
        revision_id="page.pricing.r1",
        scope=WORKSPACE_SCOPE,
        instruction_authority=InstructionAuthority.DATA,
        provenance=Provenance.AGENT_DERIVED,
    )
    entry = memory_entry(
        document=document,
        origin=MemoryOrigin.WIKI_SUMMARY,
        kind=MemoryEntryKind.FACT,
        wiki_ref=wiki_summary_ref(source_claim=source_claim),
    ).model_copy(
        update={
            "authority_ref": None,
            "source_refs": (claim_ref,),
            "wiki_ref": wiki_summary_ref(source_claim=source_claim).model_copy(
                update={"semantic_fingerprint": digest("stale")}
            ),
        }
    )
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))
    catalog = ValidationCatalog(
        evidence=(EvidenceRecord(ref=claim_ref, quote=None),),
        events=(),
        wiki_claims=(
            WikiClaimRecord(
                page_id="page.pricing", revision_id="page.pricing.r1", claim=source_claim
            ),
        ),
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="wiki_summary_stale"):
        _ = validate_memory_change(
            snapshot=snapshot,
            entries=(entry,),
            body=b"# Memory\n",
            revision_id="memory.core.r2",
            actor=actor(),
            catalog=catalog,
            at=NOW,
        )


def test_direct_to_wiki_handover_preserves_entry_id_and_canonical_claim() -> None:
    # Given
    direct = memory_entry()
    source_claim = claim()

    # When
    derived = handover_direct_entry_to_wiki(
        entry=direct,
        wiki_ref=wiki_summary_ref(source_claim=source_claim),
        text="The launch price is 29 dollars.",
    )

    # Then
    assert derived.entry_id == direct.entry_id
    assert derived.origin is MemoryOrigin.WIKI_SUMMARY
    assert derived.wiki_ref is not None
    assert derived.wiki_ref.claim_id == source_claim.claim_id


def test_soul_requires_event_brand_voice_capability_and_brand_grant() -> None:
    # Given
    document = memory_document(
        kind=MemoryKind.SOUL, document_id="memory.soul.a", brand_id="brand.a"
    )
    entry = memory_entry(document=document, soul_section=SoulSection.VOICE)
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))

    # When / Then
    with pytest.raises(ChangeValidationError, match="soul_authority_required"):
        _ = validate_memory_change(
            snapshot=snapshot,
            entries=(entry,),
            body=b"# Soul\n",
            revision_id="memory.soul.a.r2",
            actor=actor(),
            catalog=_catalog(),
            at=NOW,
        )


def test_soul_explicit_brand_voice_decision_succeeds() -> None:
    # Given
    document = memory_document(
        kind=MemoryKind.SOUL, document_id="memory.soul.a", brand_id="brand.a"
    )
    entry = memory_entry(document=document, soul_section=SoulSection.VOICE)
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))
    capabilities = (GrantCapability.WRITE, GrantCapability.BRAND_VOICE_EDIT)

    # When
    changed = validate_memory_change(
        snapshot=snapshot,
        entries=(entry,),
        body=b"# Soul\nUse a precise voice.\n",
        revision_id="memory.soul.a.r2",
        actor=actor(capabilities=capabilities, brand_id="brand.a"),
        catalog=_catalog(event_capabilities=capabilities),
        at=NOW,
    )

    # Then
    assert changed.entries[0].soul_section is SoulSection.VOICE
    assert changed.entries[0].dependency_state is DependencyState.CURRENT


def test_wiki_semantic_change_immediately_hides_old_summary() -> None:
    # Given
    original_claim = claim()
    document = memory_document()
    summary = memory_entry(
        document=document,
        origin=MemoryOrigin.WIKI_SUMMARY,
        kind=MemoryEntryKind.FACT,
        wiki_ref=wiki_summary_ref(source_claim=original_claim),
    ).model_copy(update={"authority_ref": None})
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(summary,)))
    changed_claim = original_claim.model_copy(
        update={"statement": "The launch price is 39 dollars."}
    )

    # When
    invalidated = invalidate_wiki_dependencies(
        entries=snapshot.entries,
        current_claims=(
            WikiClaimRecord(
                page_id="page.pricing",
                revision_id="page.pricing.r2",
                claim=changed_claim,
            ),
        ),
    )
    invalid_snapshot = MemorySnapshot(
        snapshot.document,
        snapshot.revision,
        invalidated,
        snapshot.body,
    )

    # Then
    assert invalidated[0].dependency_state is DependencyState.STALE
    assert visible_memory_entries(invalid_snapshot) == ()
