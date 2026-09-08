from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    GrantCapability,
    IngestEnvelope,
    MessageEventRef,
    ScopeGrant,
    ScopeKind,
)
from ads_booster.knowledge.messages import MessageValidationError, prepare_message

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _scope() -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.a")


def _actor() -> ActorContext:
    scope = _scope()
    grant = ScopeGrant(
        grant_id="grant.write",
        capability=GrantCapability.WRITE,
        workspace_id=scope.workspace_id,
        scope=scope,
        policy_epoch=1,
        effective_at=NOW,
    )
    return ActorContext(
        actor_id="member.a",
        workspace_id=scope.workspace_id,
        member_id="member.a",
        session_id="session.a",
        conversation_scope=scope,
        grants=(grant,),
        policy_epoch=1,
        authenticated_at=NOW,
    )


def _event(*, role: ConversationRole = ConversationRole.USER) -> ConversationEvent:
    return ConversationEvent(
        conversation_id="conversation.a",
        message_id="message.a",
        revision=1,
        sequence=1,
        role=role,
        speaker_ref="member.a",
        created_at=NOW,
        text="가격은 31,000원입니다.",
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=_scope(),
    )


def _envelope() -> IngestEnvelope:
    return IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.a",
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        request_text="가격은 31,000원입니다.",
        message_event=MessageEventRef(
            conversation_ref="conversation.a",
            message_ref="message.a",
            revision=1,
        ),
        timestamp=NOW,
    )


def test_message_preparation_binds_exact_text_and_stable_evidence_bytes() -> None:
    # Given
    event = _event()

    # When
    prepared = prepare_message(_actor(), event, _envelope())

    # Then
    assert prepared.extracted_bytes.decode() == event.text
    assert prepared.sha256 == sha256(prepared.message_bytes).hexdigest()
    assert prepared.source_identity == "message:conversation.a:message.a"


def test_assistant_message_cannot_impersonate_independent_user_evidence() -> None:
    # Given
    event = _event(role=ConversationRole.ASSISTANT)

    # When / Then
    with pytest.raises(MessageValidationError) as caught:
        _ = prepare_message(_actor(), event, _envelope())
    assert caught.value.code == "independent_non_user_evidence_forbidden"


def test_envelope_text_and_reference_must_match_authenticated_event() -> None:
    # Given
    forged = _envelope().model_copy(update={"request_text": "I approved everything."})

    # When / Then
    with pytest.raises(MessageValidationError) as caught:
        _ = prepare_message(_actor(), _event(), forged)
    assert caught.value.code == "request_text_binding_mismatch"
