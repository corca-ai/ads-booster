"""Canonical Agent Service ingress records and cross-database delivery outbox."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.knowledge.contracts import (
    ActorContext,
    ConversationEvent,
    ConversationEventKind,
    IngestEnvelope,
)
from ads_booster.knowledge.ingest_receipts import (
    IngestDeliveryReceipt,
    validate_ingest_delivery_receipt,
)
from ads_booster.marketing.agent_service.knowledge_ingress_schema import (
    KnowledgeIngressConflictError,
    install_ingress_schema,
    validate_ingress,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_ROW: TypeAdapter[tuple[str, ...] | None] = TypeAdapter(tuple[str, ...] | None)
_OPTIONAL_INTEGER_ROW: TypeAdapter[tuple[int | None] | None] = TypeAdapter(tuple[int | None] | None)
_PRESENCE_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_IDEMPOTENCY_TIME = datetime(1970, 1, 1, tzinfo=UTC)


class TrustedRunBinding(ContractModel):
    schema_version: Literal["trace.knowledge-run-binding.v1"] = "trace.knowledge-run-binding.v1"
    binding_id: BoundedId
    run_id: BoundedId
    request_id: BoundedId
    source: Literal["api", "slack"]
    action: Literal["create", "input"]
    source_version: str
    actor: ActorContext
    bound_at: datetime


class CanonicalIngressPayload(ContractModel):
    binding: TrustedRunBinding
    event: ConversationEvent
    envelope: IngestEnvelope


class KnowledgeIngressSink(Protocol):
    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt: ...


@runtime_checkable
class ClassifiedKnowledgeIngressError(Protocol):
    code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class PendingKnowledgeIngress:
    binding: TrustedRunBinding
    event: ConversationEvent
    envelope: IngestEnvelope


@dataclass(frozen=True, slots=True)
class CanonicalKnowledgeIngress:
    database_path: Path
    sink: KnowledgeIngressSink | None = None

    def __post_init__(self) -> None:
        """Install the additive canonical ingress schema."""
        with self.connect() as db:
            self.install(db)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def install(self, db: sqlite3.Connection) -> None:
        install_ingress_schema(db)

    def admit(
        self,
        db: sqlite3.Connection,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> bool:
        message_event = validate_ingress(binding.actor, event, envelope)
        event_key = self.event_key(event)
        payload_sha256 = contract_sha256(
            CanonicalIngressPayload(binding=binding, event=event, envelope=envelope)
        )
        previous = _ROW.validate_python(
            db.execute(
                """SELECT binding_json,event_json,envelope_json
                FROM knowledge_run_bindings AS binding
                JOIN knowledge_ingress_outbox AS outbox USING(binding_id)
                JOIN knowledge_conversation_events AS event USING(event_key)
                WHERE request_id=?""",
                (binding.request_id,),
            ).fetchone()
        )
        binding_sha256 = contract_sha256(binding)
        if previous is not None:
            stored = CanonicalIngressPayload(
                binding=TrustedRunBinding.model_validate_json(previous[0]),
                event=ConversationEvent.model_validate_json(previous[1]),
                envelope=IngestEnvelope.model_validate_json(previous[2]),
            )
            candidate = CanonicalIngressPayload(binding=binding, event=event, envelope=envelope)
            if _idempotency_sha256(stored) != _idempotency_sha256(candidate):
                raise KnowledgeIngressConflictError("knowledge_run_binding_conflict")
            return False
        outbox = _ROW.validate_python(
            db.execute(
                "SELECT payload_sha256 FROM knowledge_ingress_outbox WHERE delivery_id=?",
                (envelope.delivery_id,),
            ).fetchone()
        )
        if outbox is not None:
            if outbox[0] != payload_sha256:
                raise KnowledgeIngressConflictError("knowledge_ingress_delivery_conflict")
            return False
        latest = _OPTIONAL_INTEGER_ROW.validate_python(
            db.execute(
                "SELECT MAX(revision) FROM knowledge_conversation_events WHERE message_id=?",
                (event.message_id,),
            ).fetchone()
        )
        latest_revision = 0 if latest is None or latest[0] is None else latest[0]
        if event.revision <= latest_revision:
            raise KnowledgeIngressConflictError("knowledge_event_revision_stale")
        _ = db.execute(
            "INSERT OR IGNORE INTO knowledge_run_bindings VALUES (?,?,?,?,?)",
            (
                binding.binding_id,
                binding.run_id,
                binding.request_id,
                binding.model_dump_json(),
                binding_sha256,
            ),
        )
        _ = db.execute(
            "INSERT INTO knowledge_conversation_events VALUES (?,?,?,?,?,?,?)",
            (
                event_key,
                event.conversation_id,
                event.message_id,
                event.revision,
                event.event_kind,
                event.model_dump_json(),
                contract_sha256(event),
            ),
        )
        _ = db.execute(
            """INSERT INTO knowledge_ingress_outbox(
                delivery_id,event_key,binding_id,envelope_json,payload_sha256
            ) VALUES (?,?,?,?,?)""",
            (
                envelope.delivery_id,
                event_key,
                binding.binding_id,
                envelope.model_dump_json(),
                payload_sha256,
            ),
        )
        fence_reason: str | None = None
        if event.event_kind in {
            ConversationEventKind.MESSAGE_EDITED,
            ConversationEventKind.MESSAGE_DELETED,
        }:
            fence_reason = event.event_kind
        elif message_event.corrects_revision_ref is not None:
            fence_reason = "correction_pending"
        if fence_reason is not None:
            _ = db.execute(
                """INSERT INTO knowledge_ingress_fences
                (message_id,conversation_id,revision,reason,delivery_id,state)
                VALUES (?,?,?,?,?,'pending')
                ON CONFLICT(message_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                revision=excluded.revision,
                reason=excluded.reason,
                delivery_id=excluded.delivery_id,
                state='pending'""",
                (
                    event.message_id,
                    event.conversation_id,
                    event.revision,
                    fence_reason,
                    envelope.delivery_id,
                ),
            )
        return True

    def admit_standalone(self, ingress: PendingKnowledgeIngress) -> bool:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            return self.admit(db, ingress.binding, ingress.event, ingress.envelope)

    def dispatch_once(self) -> bool:
        if self.sink is None:
            return False
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT binding_json,event_json,envelope_json
                    FROM knowledge_ingress_outbox AS outbox
                    JOIN knowledge_run_bindings AS binding USING(binding_id)
                    JOIN knowledge_conversation_events AS event USING(event_key)
                    WHERE outbox.state='pending' ORDER BY outbox.rowid LIMIT 1"""
                ).fetchone()
            )
            if row is None:
                return False
            binding = TrustedRunBinding.model_validate_json(row[0])
            event = ConversationEvent.model_validate_json(row[1])
            envelope = IngestEnvelope.model_validate_json(row[2])
            _ = db.execute(
                """UPDATE knowledge_ingress_outbox
                SET state='dispatching',attempts=attempts+1,error_code=NULL
                WHERE delivery_id=?""",
                (envelope.delivery_id,),
            )
        try:
            receipt = self.sink.ingest(binding.actor, event, envelope)
            validate_ingest_delivery_receipt(envelope, receipt)
        except Exception as error:
            state = "dispatching"
            error_code = "knowledge_ingress_receipt_unknown"
            if isinstance(error, ClassifiedKnowledgeIngressError):
                state = "retryable" if error.retryable else "failed"
                error_code = (
                    error.code
                    if re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error.code)
                    else "knowledge_ingress_sink_failed"
                )
            with self.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                _ = db.execute(
                    """UPDATE knowledge_ingress_outbox
                    SET state=?,error_code=?
                    WHERE delivery_id=? AND state='dispatching'""",
                    (state, error_code, envelope.delivery_id),
                )
            raise
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute(
                """UPDATE knowledge_ingress_outbox SET state='acked',receipt_json=?,error_code=NULL
                WHERE delivery_id=? AND state='dispatching'""",
                (receipt.model_dump_json(), envelope.delivery_id),
            )
            _ = db.execute(
                """UPDATE knowledge_ingress_fences SET state='acked'
                WHERE delivery_id=? AND revision<=?""",
                (envelope.delivery_id, event.revision),
            )
        return True

    def recover(self) -> None:
        with self.connect() as db:
            _ = db.execute(
                "UPDATE knowledge_ingress_outbox SET state='pending' WHERE state='dispatching'"
            )

    def pending_fence(self, conversation_id: str) -> bool:
        with self.connect() as db:
            row = _PRESENCE_ROW.validate_python(
                db.execute(
                    """SELECT 1 FROM knowledge_ingress_fences
                    WHERE conversation_id=? AND state='pending' LIMIT 1""",
                    (conversation_id,),
                ).fetchone()
            )
        return row is not None

    def binding_for_run(self, run_id: str) -> TrustedRunBinding | None:
        with self.connect() as db:
            row = _ROW.validate_python(
                db.execute(
                    """SELECT binding_json FROM knowledge_run_bindings
                    WHERE run_id=? ORDER BY rowid DESC LIMIT 1""",
                    (run_id,),
                ).fetchone()
            )
        return None if row is None else TrustedRunBinding.model_validate_json(row[0])

    def pending_fence_for_run(self, run_id: str) -> bool:
        with self.connect() as db:
            row = _PRESENCE_ROW.validate_python(
                db.execute(
                    """SELECT 1 FROM knowledge_ingress_fences AS fence
                    JOIN knowledge_conversation_events AS event
                        ON event.conversation_id=fence.conversation_id
                    JOIN knowledge_ingress_outbox AS outbox USING(event_key)
                    JOIN knowledge_run_bindings AS binding USING(binding_id)
                    WHERE binding.run_id=? AND fence.state='pending' LIMIT 1""",
                    (run_id,),
                ).fetchone()
            )
        return row is not None

    @staticmethod
    def event_key(event: ConversationEvent) -> str:
        return "knowledge-event-" + contract_sha256(event)


def _idempotency_sha256(payload: CanonicalIngressPayload) -> str:
    actor = payload.binding.actor
    grants = tuple(
        grant.model_copy(update={"effective_at": _IDEMPOTENCY_TIME}) for grant in actor.grants
    )
    stable_actor = actor.model_copy(
        update={"authenticated_at": _IDEMPOTENCY_TIME, "grants": grants}
    )
    stable_binding = payload.binding.model_copy(
        update={"actor": stable_actor, "bound_at": _IDEMPOTENCY_TIME}
    )
    stable_event = payload.event.model_copy(
        update={
            "created_at": _IDEMPOTENCY_TIME,
            "edited_at": _IDEMPOTENCY_TIME if payload.event.edited_at is not None else None,
        }
    )
    stable_envelope = payload.envelope.model_copy(update={"timestamp": _IDEMPOTENCY_TIME})
    return contract_sha256(
        CanonicalIngressPayload(
            binding=stable_binding,
            event=stable_event,
            envelope=stable_envelope,
        )
    )


__all__ = [
    "CanonicalKnowledgeIngress",
    "ClassifiedKnowledgeIngressError",
    "KnowledgeIngressConflictError",
    "KnowledgeIngressSink",
    "PendingKnowledgeIngress",
    "TrustedRunBinding",
]
