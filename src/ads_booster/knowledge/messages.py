from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Never, override

from ads_booster.knowledge.contract_types import ConversationEventKind, ConversationRole

if TYPE_CHECKING:
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import ConversationEvent, IngestEnvelope


@dataclass(slots=True)
class MessageValidationError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class PreparedMessage:
    source_identity: str
    sanitized_locator: str
    message_bytes: bytes
    extracted_bytes: bytes
    sha256: str


def prepare_message(
    actor: ActorContext,
    event: ConversationEvent,
    envelope: IngestEnvelope,
) -> PreparedMessage:
    _validate_binding(actor, event, envelope)
    message_bytes = event.model_dump_json(by_alias=True).encode()
    extracted_bytes = event.text.encode()
    return PreparedMessage(
        source_identity=(f"message:{event.conversation_id}:{event.message_id}"),
        sanitized_locator=f"conversation:{event.conversation_id}/message:{event.message_id}",
        message_bytes=message_bytes,
        extracted_bytes=extracted_bytes,
        sha256=sha256(message_bytes).hexdigest(),
    )


def _validate_binding(
    actor: ActorContext,
    event: ConversationEvent,
    envelope: IngestEnvelope,
) -> None:
    require_actor_event_binding(actor, event)
    _validate_event_reference(event, envelope)
    if envelope.request_text != event.text:
        _fail("request_text_binding_mismatch")
    conversation = envelope.conversation
    if conversation is not None and (
        conversation.conversation_ref != event.conversation_id
        or conversation.message_ref != event.message_id
        or conversation.revision != event.revision
    ):
        _fail("conversation_reference_mismatch")


def require_actor_event_binding(actor: ActorContext, event: ConversationEvent) -> None:
    if event.role is not ConversationRole.USER:
        _fail("independent_non_user_evidence_forbidden")
    if event.speaker_ref != actor.actor_id:
        _fail("event_speaker_binding_mismatch")
    if event.scope != actor.conversation_scope or event.scope.workspace_id != actor.workspace_id:
        _fail("event_scope_binding_mismatch")


def _validate_event_reference(event: ConversationEvent, envelope: IngestEnvelope) -> None:
    if envelope.event_kind is not event.event_kind:
        _fail("event_kind_binding_mismatch")
    message_ref = envelope.message_event
    if message_ref is None:
        _fail("message_event_reference_missing")
    if (
        message_ref.conversation_ref != event.conversation_id
        or message_ref.message_ref != event.message_id
        or message_ref.revision != event.revision
    ):
        _fail("message_event_reference_mismatch")
    if envelope.timestamp < event.created_at:
        _fail("delivery_precedes_event")
    if event.event_kind is ConversationEventKind.MESSAGE_EDITED and (
        event.edited_at is None or envelope.timestamp < event.edited_at
    ):
        _fail("delivery_precedes_edit")


def _fail(code: str) -> Never:
    raise MessageValidationError(code)


__all__ = [
    "MessageValidationError",
    "PreparedMessage",
    "prepare_message",
    "require_actor_event_binding",
]
