"""Atomic terminal-experience admission from the canonical Agent Run ledger."""

from __future__ import annotations

# ruff: noqa: EM101
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, overload, override

from pydantic import TypeAdapter, ValidationError

from ads_booster.agent.service.knowledge_ingress import TrustedLearningSource, TrustedRunBinding
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.canonical import canonical_json
from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.ingest_receipts import IngestDeliveryReceipt, IngestUnitKind
from ads_booster.knowledge.learning_contracts import (
    EXPERIENCE_INPUT_EXCERPT_BYTES,
    EXPERIENCE_OUTPUT_EXCERPT_BYTES,
    AgentExperienceEvidence,
    AgentExperienceRef,
    ExperienceEvidenceCompleteness,
    ExperienceOutcome,
)
from ads_booster.knowledge.source_contracts import ConversationEvent, IngestReceipt
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.service.sqlite_repository import RepositoryAdmission
    from ads_booster.knowledge.repository_learning import LearningReviewCoordinator

_ROW: TypeAdapter[tuple[str, ...] | None] = TypeAdapter(tuple[str, ...] | None)
_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])
_ACKED_SOURCE_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_LEARNING_CAPABILITY_PREFIXES = ("knowledge_", "memory_", "skill_", "source_")
_PENDING_ROW_FIELD_COUNT = 5


@dataclass(slots=True)
class LearningAdmissionError(Exception):
    """A terminal receipt conflicts with canonical stored authority or replay state."""

    code: str
    retryable: bool = False

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class _NoTerminalExperience:
    def __call__(self, connection: sqlite3.Connection) -> None:
        """Leave excluded receipts outside the learning outbox."""


@dataclass(frozen=True, slots=True)
class _TerminalExperienceWrite:
    binding: TrustedRunBinding
    event: ConversationEvent | None
    source_receipt: IngestReceipt
    receipt: ToolReceiptRecord
    source_digest: str
    capability_id: str

    def __call__(self, connection: sqlite3.Connection) -> None:
        source = _resolve_stored_source(connection, self.binding, self.event, self.source_receipt)
        if source.event.scope.kind is not ScopeKind.WORKSPACE:
            return
        invocation, evidence = _validate_stored_receipt(
            connection, source, self.receipt, self.capability_id
        )
        experience = AgentExperienceRef(
            schema="knowledge.agent-experience-ref.v1",
            experience_id="experience."
            + contract_sha256(
                {
                    "receipt_id": self.receipt.receipt_id,
                    "source_digest": self.source_digest,
                    "capability_id": self.capability_id,
                }
            ),
            workspace_id=source.binding.actor.workspace_id,
            run_id=source.binding.run_id,
            invocation_id=invocation.invocation_id,
            receipt_id=self.receipt.receipt_id,
            source_digest=self.source_digest,
            outcome=_outcome(self.receipt),
            capability_id=self.capability_id,
            occurred_at=self.receipt.occurred_at,
            applicability=AppliesTo(),
            evidence=evidence,
        )
        try:
            _ = connection.execute(
                """INSERT INTO agent_learning_experience_outbox(
                    experience_id,run_id,receipt_id,binding_json,event_json,
                    source_receipt_json,experience_json,state,created_at
                ) VALUES (?,?,?,?,?,?,?,'pending',?)""",
                (
                    experience.experience_id,
                    experience.run_id,
                    self.receipt.receipt_id,
                    source.binding.model_dump_json(),
                    source.event.model_dump_json(),
                    source.receipt.model_dump_json(),
                    experience.model_dump_json(),
                    experience.occurred_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise LearningAdmissionError("learning_receipt_replay") from error


@dataclass(frozen=True, slots=True)
class TerminalExperienceAdmission:
    """Persist and deliver receipt-grounded experiences without forging user events."""

    coordinator: LearningReviewCoordinator

    @overload
    def for_receipt(
        self,
        source: TrustedLearningSource,
        source_receipt: ToolReceiptRecord,
        *,
        capability_id: str | None = None,
    ) -> RepositoryAdmission: ...

    @overload
    def for_receipt(
        self,
        source: TrustedRunBinding,
        source_receipt: IngestReceipt,
        receipt: ToolReceiptRecord,
        *,
        capability_id: str | None = None,
    ) -> RepositoryAdmission: ...

    def for_receipt(
        self,
        source: TrustedLearningSource | TrustedRunBinding,
        source_receipt: IngestReceipt | ToolReceiptRecord,
        receipt: ToolReceiptRecord | None = None,
        *,
        capability_id: str | None = None,
    ) -> RepositoryAdmission:
        """Build an in-transaction write from a trusted source and terminal receipt."""
        match source, source_receipt, receipt:
            case TrustedLearningSource() as trusted, ToolReceiptRecord() as terminal, None:
                binding = trusted.binding
                event: ConversationEvent | None = trusted.event
                ingest_receipt = trusted.receipt
            case (
                TrustedRunBinding() as binding,
                IngestReceipt() as ingest_receipt,
                ToolReceiptRecord() as terminal,
            ):
                event = None
            case _:
                raise LearningAdmissionError("learning_receipt_arguments_invalid")
        selected_capability = capability_id or terminal.executor_id
        if not _eligible(binding, selected_capability):
            return _NoTerminalExperience()
        stored = self.coordinator.repository.read_source(binding.actor, ingest_receipt.source_id)
        if stored is None or stored.source.revision_id != ingest_receipt.source_revision_id:
            raise LearningAdmissionError("learning_source_revision_stale")
        return _TerminalExperienceWrite(
            binding,
            event,
            ingest_receipt,
            terminal,
            stored.source.sha256,
            selected_capability,
        )

    def recover(self, database_path: Path) -> None:
        """Idempotently deliver pending experiences to the learning coordinator."""
        while self.dispatch_once(database_path):
            pass

    def dispatch_once(self, database_path: Path) -> bool:
        """Deliver and acknowledge at most one pending experience."""
        pending = _next_pending(database_path)
        if pending is None:
            return False
        experience_id, binding_json, event_json, receipt_json, experience_json = pending
        try:
            try:
                binding = TrustedRunBinding.model_validate_json(binding_json)
                event = _conversation_event(event_json)
                source_receipt = _ingest_receipt(receipt_json)
                experience = AgentExperienceRef.model_validate_json(experience_json)
            except ValidationError as error:
                raise LearningAdmissionError("learning_outbox_payload_invalid") from error
            _ = self.coordinator.admit_experience(
                binding,
                event,
                source_receipt,
                experience,
                at=experience.occurred_at,
            )
            _ack(database_path, experience_id)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise LearningAdmissionError(
                    "learning_outbox_delivery_retryable", retryable=True
                ) from error
            raise
        return True

    def after_commit(self, database_path: Path) -> None:
        """Drain only after the canonical receipt transaction has committed."""
        self.recover(database_path)

    def pending(self, run_id: str) -> tuple[AgentExperienceRef, ...]:
        """Return coordinator-admitted experiences for a canonical run."""
        return self.coordinator.experiences_for_run(run_id)


def _eligible(
    binding: TrustedRunBinding,
    capability_id: str,
) -> bool:
    return not binding.run_id.startswith("background.") and not capability_id.startswith(
        _LEARNING_CAPABILITY_PREFIXES
    )


def _outcome(receipt: ToolReceiptRecord) -> ExperienceOutcome:
    match receipt.disposition:
        case "succeeded":
            return ExperienceOutcome.SUCCEEDED
        case "failed":
            return ExperienceOutcome.FAILED
        case "unknown_side_effect":
            return ExperienceOutcome.UNKNOWN_SIDE_EFFECT
        case "no_effect":
            return ExperienceOutcome.OBSERVED


def _validate_stored_receipt(
    connection: sqlite3.Connection,
    source: TrustedLearningSource,
    receipt: ToolReceiptRecord,
    capability_id: str,
) -> tuple[ToolInvocation, AgentExperienceEvidence]:
    run = _ROW.validate_python(
        connection.execute(
            "SELECT tenant_id FROM agent_runs WHERE run_id=?", (source.binding.run_id,)
        ).fetchone()
    )
    if run is None or run[0] != source.binding.actor.workspace_id:
        raise LearningAdmissionError("learning_run_binding_missing")
    invocation_row = _ROW.validate_python(
        connection.execute(
            """SELECT record_json FROM agent_records
            WHERE run_id=? AND kind=? AND json_extract(record_json,'$.payload_sha256')=?""",
            (
                source.binding.run_id,
                AgentRecordKind.INVOCATION.value,
                receipt.invocation_sha256,
            ),
        ).fetchone()
    )
    if invocation_row is None:
        raise LearningAdmissionError("learning_invocation_missing")
    invocation_record = AgentRecord.model_validate_json(invocation_row[0])
    invocation = ToolInvocation.model_validate(invocation_record.payload)
    receipt_row = _ROW.validate_python(
        connection.execute(
            "SELECT record_json FROM agent_records WHERE run_id=? AND record_id=?",
            (source.binding.run_id, receipt.receipt_id),
        ).fetchone()
    )
    if receipt_row is None:
        raise LearningAdmissionError("learning_terminal_receipt_missing")
    stored_record = AgentRecord.model_validate_json(receipt_row[0])
    stored_receipt = ToolReceiptRecord.model_validate(stored_record.payload)
    if stored_receipt != receipt or invocation.run_id != source.binding.run_id:
        raise LearningAdmissionError("learning_terminal_receipt_conflict")
    if invocation.tenant_id is not None and invocation.tenant_id != run[0]:
        raise LearningAdmissionError("learning_invocation_tenant_conflict")
    return invocation, _experience_evidence(connection, source, invocation, receipt, capability_id)


def _experience_evidence(
    connection: sqlite3.Connection,
    source: TrustedLearningSource,
    invocation: ToolInvocation,
    receipt: ToolReceiptRecord,
    capability_id: str,
) -> AgentExperienceEvidence:
    rows = _ROWS.validate_python(
        connection.execute(
            """SELECT record_json FROM agent_records
            WHERE run_id=? AND kind=? ORDER BY rowid""",
            (source.binding.run_id, AgentRecordKind.EVIDENCE.value),
        ).fetchall()
    )
    receipt_sha256 = contract_sha256(receipt)
    outputs: list[JsonObject] = []
    for row in rows:
        record = AgentRecord.model_validate_json(row[0])
        if (
            record.payload_schema_version != "trace.tool-output-evidence.v1"
            or record.payload.get("receipt_sha256") != receipt_sha256
        ):
            continue
        if record.payload.get("capability_id") != capability_id:
            raise LearningAdmissionError("learning_output_capability_conflict")
        outputs.append(_JSON_OBJECT.validate_python(record.payload.get("output")))
    if len(outputs) > 1:
        raise LearningAdmissionError("learning_output_evidence_conflict")
    output = None if not outputs else outputs[0]
    if output is not None and contract_sha256(output) != receipt.output_sha256:
        raise LearningAdmissionError("learning_output_digest_conflict")
    input_excerpt, input_completeness = _bounded_excerpt(
        invocation.input, EXPERIENCE_INPUT_EXCERPT_BYTES
    )
    output_excerpt, output_completeness = (
        (None, ExperienceEvidenceCompleteness.UNAVAILABLE)
        if output is None
        else _bounded_excerpt(output, EXPERIENCE_OUTPUT_EXCERPT_BYTES)
    )
    return AgentExperienceEvidence(
        schema="knowledge.agent-experience-evidence.v1",
        input_sha256=invocation.input_sha256,
        output_sha256=receipt.output_sha256,
        input_json_excerpt=input_excerpt,
        output_json_excerpt=output_excerpt,
        input_completeness=input_completeness,
        output_completeness=output_completeness,
    )


def _bounded_excerpt(
    value: JsonObject,
    byte_limit: int,
) -> tuple[str, ExperienceEvidenceCompleteness]:
    payload = canonical_json(value)
    encoded = payload.encode()
    if len(encoded) <= byte_limit:
        return payload, ExperienceEvidenceCompleteness.COMPLETE
    return (
        encoded[:byte_limit].decode(errors="ignore"),
        ExperienceEvidenceCompleteness.TRUNCATED,
    )


def _resolve_stored_source(
    connection: sqlite3.Connection,
    binding: TrustedRunBinding,
    expected_event: ConversationEvent | None,
    source_receipt: IngestReceipt,
) -> TrustedLearningSource:
    bindings = _ROWS.validate_python(
        connection.execute(
            """SELECT binding_json FROM knowledge_run_bindings WHERE run_id=?
            UNION ALL SELECT binding_json FROM knowledge_execution_bindings WHERE run_id=?""",
            (binding.run_id, binding.run_id),
        ).fetchall()
    )
    if not any(TrustedRunBinding.model_validate_json(row[0]) == binding for row in bindings):
        raise LearningAdmissionError("learning_stored_binding_conflict")
    rows = _ACKED_SOURCE_ROWS.validate_python(
        connection.execute(
            """SELECT event.event_json,outbox.receipt_json
            FROM knowledge_ingress_outbox AS outbox
            JOIN knowledge_conversation_events AS event USING(event_key)
            JOIN knowledge_run_bindings AS admitted USING(binding_id)
            LEFT JOIN knowledge_execution_bindings AS execution
                ON execution.message_id=event.message_id
            WHERE outbox.state='acked' AND outbox.receipt_json IS NOT NULL
                AND (admitted.run_id=? OR execution.run_id=?)
            ORDER BY event.revision DESC,outbox.rowid DESC""",
            (binding.run_id, binding.run_id),
        ).fetchall()
    )
    for event_json, delivery_json in rows:
        event = _conversation_event(event_json)
        if expected_event is not None and event != expected_event:
            continue
        delivery = IngestDeliveryReceipt.model_validate_json(delivery_json)
        if any(
            unit.kind is IngestUnitKind.MESSAGE and unit.receipt == source_receipt
            for unit in delivery.unit_receipts
        ):
            return TrustedLearningSource(binding, event, source_receipt)
    raise LearningAdmissionError("learning_stored_source_conflict")


def _next_pending(database_path: Path) -> tuple[str, str, str, str, str] | None:
    with closing(sqlite3.connect(database_path)) as connection:
        row = _ROW.validate_python(
            connection.execute(
                """SELECT experience_id,binding_json,event_json,source_receipt_json,experience_json
                FROM agent_learning_experience_outbox WHERE state='pending'
                ORDER BY created_at,experience_id LIMIT 1"""
            ).fetchone()
        )
    if row is None:
        return None
    if len(row) != _PENDING_ROW_FIELD_COUNT:
        raise LearningAdmissionError("learning_outbox_row_invalid")
    return row[0], row[1], row[2], row[3], row[4]


def _ack(database_path: Path, experience_id: str) -> None:
    with closing(sqlite3.connect(database_path)) as connection, connection:
        cursor = connection.execute(
            """UPDATE agent_learning_experience_outbox SET state='acked'
            WHERE experience_id=? AND state='pending'""",
            (experience_id,),
        )
        if cursor.rowcount == 1:
            return
        row = _ROW.validate_python(
            connection.execute(
                "SELECT state FROM agent_learning_experience_outbox WHERE experience_id=?",
                (experience_id,),
            ).fetchone()
        )
    if row is None or row[0] != "acked":
        raise LearningAdmissionError("learning_outbox_ack_conflict")


def _conversation_event(payload: str) -> ConversationEvent:
    return ConversationEvent.model_validate_json(payload)


def _ingest_receipt(payload: str) -> IngestReceipt:
    return IngestReceipt.model_validate_json(payload)


__all__ = ["LearningAdmissionError", "TerminalExperienceAdmission"]
