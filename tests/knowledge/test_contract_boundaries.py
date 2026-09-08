from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    AccessScope,
    Applicability,
    AppliesTo,
    AuthenticatedEvent,
    AuthorityClass,
    AuthorityRef,
    Brand,
    BrandEvent,
    BrandEventKind,
    BrandState,
    Claim,
    ClaimKind,
    ClaimStatus,
    ConstraintBinding,
    ConstraintCompatibility,
    EvidenceKind,
    EvidenceRef,
    IngestEnvelope,
    KnowledgeActionKind,
    KnowledgeRevision,
    MemoryDocument,
    PageRelation,
    PageRelationKind,
    Provenance,
    QuoteRange,
    ScopeKind,
    SegmentLocator,
    SourceCompleteness,
    SourceSegment,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.errors import EvidenceResolutionError

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
DIGEST = "a" * 64


def _scope() -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-a")


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_kind=EvidenceKind.SOURCE_SEGMENT,
        evidence_id="segment.price.1",
        revision_id="source.price.rev1",
        scope=_scope(),
    )


def _authority() -> AuthorityRef:
    return AuthorityRef(
        event_id="event.decision.1",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="member.a1",
        workspace_id="workspace.team-a",
        scope=_scope(),
        policy_epoch=7,
    )


def test_ingest_wire_roundtrip_preserves_schema_alias_and_digest() -> None:
    payload = (
        '{"schema":"knowledge.ingest-envelope.v1","delivery_id":"delivery.1",'
        '"event_kind":"message_finalized","request_text":"remember",'
        '"message_event":{"conversation_ref":"conversation.1","message_ref":"message.1",'
        '"revision":1,"logical_source_ref":"source.1",'
        '"logical_revision_ref":"source.1.rev1"},"attachments":[],"timestamp":'
        '"2026-09-07T10:00:00Z"}'
    )
    envelope = IngestEnvelope.model_validate_json(payload)

    roundtrip = IngestEnvelope.model_validate_json(envelope.model_dump_json())

    assert roundtrip == envelope
    assert '"schema":"knowledge.ingest-envelope.v1"' in envelope.model_dump_json()
    assert contract_sha256(roundtrip) == contract_sha256(envelope)


def test_source_segment_rejects_reversed_locator_and_quote_ranges() -> None:
    with pytest.raises(ValidationError, match="segment_line_range_not_ordered"):
        _ = SegmentLocator(line_start=9, line_end=3)


def test_applicability_preserves_future_effective_period() -> None:
    current = Applicability(effective_until=date(2026, 9, 14), markets=("KR",))
    scheduled = Applicability(effective_from=date(2026, 9, 15), markets=("KR",))

    assert current.effective_until == date(2026, 9, 14)
    assert scheduled.effective_from == date(2026, 9, 15)
    with pytest.raises(ValidationError, match="applicability_period_not_ordered"):
        _ = Applicability(
            effective_from=date(2026, 9, 15),
            effective_until=date(2026, 9, 14),
        )


def test_memory_rejects_invalid_calendar_date_and_timezone() -> None:
    given = (
        '{"document_id":"memory.daily.1","workspace_id":"workspace.team-a",'
        '"kind":"daily","local_date":"2026-02-30","timezone":"Mars/Olympus",'
        '"head_revision_id":"memory.daily.rev1"}'
    )

    with pytest.raises(ValidationError) as caught:
        _ = MemoryDocument.model_validate_json(given)

    assert {item["type"] for item in caught.value.errors()} == {
        "date_from_datetime_parsing",
        "invalid_iana_timezone",
    }


def test_wiki_revision_preserves_claim_relation_and_evidence_scope() -> None:
    claim = Claim(
        claim_id="claim.price.current",
        kind=ClaimKind.FACT,
        statement="The source states KRW 31,000 effective September 15.",
        applicability=Applicability(
            markets=("KR",),
            effective_from=date(2026, 9, 15),
        ),
        status=ClaimStatus.ACTIVE,
        evidence_refs=(_evidence(),),
        admission_reason="Exact price source quote.",
        observed_at=NOW,
    )
    relation = PageRelation(
        relation_id="relation.price.plan",
        from_page_id="page.price",
        to_page_id="page.plan",
        kind=PageRelationKind.RELATED_TO,
        reason="The price belongs to the plan.",
    )
    revision = KnowledgeRevision(
        page_id="page.price",
        revision_id="page.price.rev2",
        previous_revision_id="page.price.rev1",
        body_sha256=DIGEST,
        title="Price",
        claims=(claim,),
        relations=(relation,),
        scope=_scope(),
    )

    assert revision.claims[0].applicability == claim.applicability
    assert revision.relations[0].kind is PageRelationKind.RELATED_TO


def test_brand_constraint_and_task_bindings_remain_workspace_bound() -> None:
    brand = Brand(
        brand_id="brand.a",
        workspace_id="workspace.team-a",
        name="Synthetic Brand A",
        revision=1,
        state=BrandState.ACTIVE,
    )
    event = BrandEvent(
        event_id="brand.event.1",
        kind=BrandEventKind.REGISTERED,
        brand_id=brand.brand_id,
        workspace_id=brand.workspace_id,
        authority_ref=_authority(),
        occurred_at=NOW,
    )
    constraint = ConstraintBinding(
        constraint_id="constraint.citation.current",
        workspace_id=brand.workspace_id,
        entry_id="entry.team.citation",
        revision_id="memory.team.rev1",
        applies_to=AppliesTo(action_kinds=(KnowledgeActionKind.RESEARCH,)),
        authority_ref=_authority(),
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        compatibility=ConstraintCompatibility.COMPATIBLE,
    )
    task = TaskBinding(
        task_id="task.1",
        workspace_id=brand.workspace_id,
        actor_ref="member.a1",
        member_id="member.a1",
        session_id="session.1",
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        brand_id=brand.brand_id,
        brand_catalog_revision=brand.revision,
        capability_epoch=7,
        state=TaskBindingState.ACTIVE,
        opened_at=NOW,
    )

    assert event.workspace_id == constraint.workspace_id
    assert task.brand_catalog_revision == brand.revision


def test_brand_event_rejects_cross_workspace_authority_payload() -> None:
    other_scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-b")
    other_authority = AuthorityRef(
        event_id="event.decision.team-b",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="member.b1",
        workspace_id="workspace.team-b",
        scope=other_scope,
        policy_epoch=7,
    )

    with pytest.raises(ValidationError, match="brand_event_authority_workspace_mismatch"):
        _ = BrandEvent(
            event_id="brand.event.forged",
            kind=BrandEventKind.REGISTERED,
            brand_id="brand.a",
            workspace_id="workspace.team-a",
            authority_ref=other_authority,
            occurred_at=NOW,
        )


def test_authority_models_reject_workspace_scope_mismatch() -> None:
    other_scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-b")

    with pytest.raises(ValidationError, match="authority_scope_workspace_mismatch"):
        _ = AuthorityRef(
            event_id="event.forged",
            authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
            actor_ref="member.a1",
            workspace_id="workspace.team-a",
            scope=other_scope,
            policy_epoch=7,
        )

    with pytest.raises(ValidationError, match="authenticated_event_scope_workspace_mismatch"):
        _ = AuthenticatedEvent(
            event_id="event.forged",
            actor_ref="member.a1",
            workspace_id="workspace.team-a",
            scope=other_scope,
            provenance=Provenance.HUMAN_DIRECT,
            authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
            policy_epoch=7,
            occurred_at=NOW,
        )


def test_evidence_resolution_error_has_stable_missing_evidence_code() -> None:
    error = EvidenceResolutionError(
        evidence_id="missing.evidence.id",
        revision_id="missing.evidence.rev1",
    )

    assert error.code == "missing_evidence"
    assert str(error) == (
        "missing_evidence: evidence=missing.evidence.id revision=missing.evidence.rev1"
    )


def test_source_segment_has_bounded_exact_quote_provenance() -> None:
    segment = SourceSegment(
        source_id="source.price.kr",
        revision_id="source.price.kr.rev2",
        segment_id="segment.price.kr.2",
        content_sha256=DIGEST,
        extraction_version="extractor.text.v1",
        locator=SegmentLocator(line_start=1, line_end=1),
        quote_range=QuoteRange(start=0, end=20),
        completeness=SourceCompleteness.FULL,
    )

    assert segment.quote_range.end == 20
