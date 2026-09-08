from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import RootModel, ValidationError

from ads_booster.contracts.knowledge_selection import (
    ContextRequest,
    KnowledgeActionKind,
)
from ads_booster.knowledge.contracts import (
    AccessScope,
    AuthorityClass,
    AuthorityRef,
    Claim,
    ClaimKind,
    ClaimStatus,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    EvidenceKind,
    EvidenceRef,
    IngestEnvelope,
    MemoryDocument,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    ScopeKind,
    SoulSection,
    Source,
    SourceCompleteness,
    SourceDisposition,
    SourceExtractionStatus,
    SourceKind,
    UsageRole,
)
from ads_booster.transport.json_types import JsonObject

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
DIGEST = "a" * 64


class JsonObjectModel(RootModel[JsonObject]):
    pass


def _workspace_scope() -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-a")


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_kind=EvidenceKind.SOURCE_SEGMENT,
        evidence_id="segment.price.kr.1",
        revision_id="source.price.kr.rev1",
        scope=_workspace_scope(),
    )


def _authority() -> AuthorityRef:
    return AuthorityRef(
        event_id="event.team.decision.1",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="member.a1",
        workspace_id="workspace.team-a",
        scope=_workspace_scope(),
        policy_epoch=7,
    )


def test_context_request_parses_frozen_payload_only_after_trust_is_separated() -> None:
    given = JsonObjectModel.model_validate_json(
        (FIXTURE_ROOT / "context-request.json").read_text()
    ).root
    trusted = given.pop("trusted_binding_refs")

    when = ContextRequest.model_validate(given)

    assert when.action_kind is KnowledgeActionKind.RESEARCH
    assert when.task_ref == "task.fixture.price-check"
    assert trusted == {"workspace_ref": "workspace.team-a", "actor_ref": "member.a1"}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _ = ContextRequest.model_validate({**given, "workspace_id": "workspace.forged"})


def test_ingest_envelope_parses_frozen_conversation_event() -> None:
    given = JsonObjectModel.model_validate_json(
        (FIXTURE_ROOT / "message-decision.json").read_text()
    ).root

    when = IngestEnvelope.model_validate(given)

    assert when.message_event is not None
    assert when.message_event.message_ref == "message.launch.decision"
    assert when.attachments == ()


def test_contracts_reject_extra_fields_and_naive_time() -> None:
    given: JsonObject = {
        "schema": "knowledge.ingest-envelope.v1",
        "delivery_id": "delivery.1",
        "event_kind": "message_finalized",
        "request_text": "remember this",
        "timestamp": "2026-09-07T10:00:00",
        "message_event": {
            "conversation_ref": "conversation.1",
            "message_ref": "message.1",
            "revision": 1,
            "logical_source_ref": "source.1",
            "logical_revision_ref": "source.1.rev1",
        },
        "attachments": [],
        "approved": True,
    }

    with pytest.raises(ValidationError) as caught:
        _ = IngestEnvelope.model_validate(given)

    error_types = {error["type"] for error in caught.value.errors()}
    assert error_types == {"extra_forbidden", "knowledge_timestamp_not_utc"}


@pytest.mark.parametrize(
    ("kind", "brand_id", "local_date", "valid"),
    [
        (MemoryKind.TEAM, None, None, True),
        (MemoryKind.CORE, None, None, True),
        (MemoryKind.SOUL, "brand.a", None, True),
        (MemoryKind.SOUL, None, None, False),
        (MemoryKind.DAILY, None, date(2026, 9, 7), True),
        (MemoryKind.DAILY, "brand.a", date(2026, 9, 7), False),
        (MemoryKind.TEAM, None, date(2026, 9, 7), False),
    ],
)
def test_memory_document_encodes_kind_identity(
    kind: MemoryKind,
    brand_id: str | None,
    local_date: date | None,
    valid: bool,
) -> None:
    given = {
        "document_id": f"memory.{kind.value}.1",
        "workspace_id": "workspace.team-a",
        "kind": kind,
        "brand_id": brand_id,
        "local_date": local_date,
        "timezone": "Asia/Seoul",
        "head_revision_id": "memory.rev1",
    }

    if valid:
        assert MemoryDocument.model_validate(given).kind is kind
    else:
        with pytest.raises(ValidationError, match="invalid_memory"):
            _ = MemoryDocument.model_validate(given)


def test_soul_entry_requires_direct_authorized_decision() -> None:
    given = {
        "entry_id": "entry.soul.voice.1",
        "document_id": "memory.soul.1",
        "document_kind": "soul",
        "text": "Use short, concrete sentences.",
        "kind": "observation",
        "status": "active",
        "dependency_state": "current",
        "origin": "direct",
        "usage_role": "reference",
        "scope": _workspace_scope(),
        "source_refs": (_evidence(),),
        "soul_section": "voice",
        "authority_ref": None,
        "admission_reason": "Observed in one post.",
    }

    with pytest.raises(ValidationError, match="soul_active_entry_requires_direct_decision"):
        _ = MemoryEntry.model_validate(given)

    valid = MemoryEntry.model_validate(
        {
            **given,
            "kind": MemoryEntryKind.DECISION,
            "usage_role": UsageRole.CONSTRAINT,
            "authority_ref": _authority(),
        }
    )
    assert valid.soul_section is SoulSection.VOICE
    assert valid.origin is MemoryOrigin.DIRECT
    assert valid.status is MemoryStatus.ACTIVE


def test_claim_kind_is_explicit_without_truth_certification() -> None:
    fact = Claim(
        claim_id="claim.price.kr.current",
        kind=ClaimKind.FACT,
        statement="The observed source states KRW 31,000.",
        status=ClaimStatus.ACTIVE,
        evidence_refs=(_evidence(),),
        admission_reason="Quoted from the current price source.",
        observed_at=NOW,
    )
    inference = fact.model_copy(
        update={"claim_id": "claim.price.inference", "kind": ClaimKind.INFERENCE}
    )

    assert fact.kind is ClaimKind.FACT
    assert inference.kind is ClaimKind.INFERENCE
    assert "truth" not in Claim.model_fields
    with pytest.raises(ValidationError, match="decision_requires_authority"):
        _ = Claim.model_validate({**fact.model_dump(), "kind": "decision"})


def test_source_and_conversation_preserve_scope_and_utc() -> None:
    scope = _workspace_scope()
    source = Source(
        source_id="source.price.kr",
        workspace_id="workspace.team-a",
        scope=scope,
        owner_ref="member.a1",
        source_kind=SourceKind.MESSAGE,
        sanitized_locator="conversation.team-a.seed/message.team-a.seed-price",
        source_identity="slack:message.team-a.seed-price",
        revision_id="source.price.kr.rev1",
        revision=1,
        fetched_at=NOW,
        sha256=DIGEST,
        mime_type="text/plain",
        byte_length=42,
        extraction_status=SourceExtractionStatus.COMPLETE,
        completeness=SourceCompleteness.FULL,
        extractor_version="message.v1",
        disposition=SourceDisposition.PENDING,
        admission_revision=0,
    )
    event = ConversationEvent(
        conversation_id="conversation.team-a.seed",
        message_id="message.team-a.seed-price",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref="member.a1",
        created_at=NOW,
        text="가격은 31,000원입니다.",
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=scope,
    )

    assert source.scope == event.scope
    assert event.created_at.utcoffset() == UTC.utcoffset(None)
