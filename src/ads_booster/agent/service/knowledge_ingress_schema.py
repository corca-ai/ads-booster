from __future__ import annotations

import sqlite3  # noqa: TC003 - public connection annotation.
from typing import cast

from ads_booster.knowledge.contracts import (
    ActorContext,
    ConversationEvent,
    IngestEnvelope,
    MessageEventRef,
)


class KnowledgeIngressConflictError(ValueError):
    """The delivery conflicts with immutable canonical ingress state."""


def install_ingress_schema(db: sqlite3.Connection) -> None:
    _ = db.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_run_bindings (
            binding_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            binding_json TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL,
            UNIQUE(request_id)
        );
        CREATE TABLE IF NOT EXISTS knowledge_conversation_events (
            event_key TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            event_kind TEXT NOT NULL,
            event_json TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            UNIQUE(message_id, revision)
        );
        CREATE INDEX IF NOT EXISTS knowledge_event_message_revision
            ON knowledge_conversation_events(message_id, revision DESC);
        CREATE TABLE IF NOT EXISTS knowledge_ingress_outbox (
            delivery_id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL REFERENCES knowledge_conversation_events(event_key),
            binding_id TEXT NOT NULL REFERENCES knowledge_run_bindings(binding_id),
            envelope_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            receipt_json TEXT,
            error_code TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS knowledge_ingress_fences (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            reason TEXT NOT NULL,
            delivery_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    columns = {
        str(row[1])
        for row in cast(
            "list[tuple[object, ...]]",
            db.execute("PRAGMA table_info(knowledge_ingress_outbox)").fetchall(),
        )
    }
    if "attempts" not in columns:
        _ = db.execute(
            "ALTER TABLE knowledge_ingress_outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
        )


def validate_ingress(
    actor: ActorContext,
    event: ConversationEvent,
    envelope: IngestEnvelope,
) -> MessageEventRef:
    if actor.workspace_id != event.scope.workspace_id:
        raise KnowledgeIngressConflictError("knowledge_ingress_actor_scope_conflict")
    if envelope.message_event is None:
        raise KnowledgeIngressConflictError("knowledge_ingress_message_ref_required")
    if (
        envelope.message_event.conversation_ref != event.conversation_id
        or envelope.message_event.message_ref != event.message_id
        or envelope.message_event.revision != event.revision
        or envelope.event_kind is not event.event_kind
    ):
        raise KnowledgeIngressConflictError("knowledge_ingress_event_envelope_conflict")
    return envelope.message_event


__all__ = [
    "KnowledgeIngressConflictError",
    "install_ingress_schema",
    "validate_ingress",
]
