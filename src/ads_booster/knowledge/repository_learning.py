"""Durable workspace learning cadence over existing source curation jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, Never, override

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import (
    BatchCurationCoordinator,
    CurationBatchItem,
    curation_partition_key,
)
from ads_booster.knowledge.contract_types import (
    ConversationEventKind,
    ConversationRole,
    EvidenceKind,
    InstructionAuthority,
    Provenance,
    ScopeKind,
)
from ads_booster.knowledge.contracts import CurationBatch, KnowledgeJob
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.identifiers import stable_id
from ads_booster.knowledge.learning_contracts import (
    AgentExperienceRef,
    ExperienceOutcome,
    LearningCounter,
    LearningPurpose,
    LearningReviewRequest,
    LearningRound,
    LearningRoundState,
)
from ads_booster.knowledge.learning_policy import LEARNING_POLICY_VERSION
from ads_booster.knowledge.messages import require_actor_event_binding
from ads_booster.knowledge.operation_enums import BatchState, JobState
from ads_booster.knowledge.scope_contracts import ActorContext
from ads_booster.knowledge.source_contracts import ConversationEvent

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.agent.service.knowledge_ingress import (
        TrustedLearningSource,
        TrustedRunBinding,
    )
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.source_contracts import IngestReceipt

_THRESHOLD = 10
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
type AdmissionKind = Literal["turn", "experience"]
type ReviewRow = tuple[str | None, str | None, str, str, str]
type SealRow = tuple[str, str, str, str | None, str]
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_OPTIONAL_NULLABLE_STRING_ROW: TypeAdapter[tuple[str | None] | None] = TypeAdapter(
    tuple[str | None] | None
)
_OPTIONAL_STRING_PAIR_ROW: TypeAdapter[tuple[str, str] | None] = TypeAdapter(tuple[str, str] | None)
_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_STRING_PAIR_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])
_REVIEW_ROWS: TypeAdapter[list[ReviewRow]] = TypeAdapter(list[ReviewRow])
_SEAL_ROWS: TypeAdapter[list[SealRow]] = TypeAdapter(list[SealRow])


@dataclass(slots=True)
class LearningRepositoryError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class LearningAdmission:
    ready_batch_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _AdmissionSource:
    actor: ActorContext
    run_id: str
    event: ConversationEvent
    receipt: IngestReceipt
    source_digest: str
    job: KnowledgeJob
    batch: CurationBatch
    partition_key: str


@dataclass(frozen=True, slots=True)
class _AdmissionWrite:
    kind: AdmissionKind
    watermark: str
    event: ConversationEvent | None
    experience: AgentExperienceRef | None
    at: datetime
    urgent: bool
    counted: bool


@dataclass(frozen=True, slots=True)
class _SealWindow:
    purpose: LearningPurpose
    at: datetime
    only_admission_id: str | None
    preserve_counter: bool


@dataclass(frozen=True, slots=True)
class LearningReviewCoordinator:
    repository: SqliteKnowledgeRepository

    def admit_turn(
        self,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        source_receipt: IngestReceipt,
        *,
        at: datetime,
        urgent: bool = False,
    ) -> LearningAdmission:
        """Count one canonical completed shared-user turn and seal at ten."""
        if not self._eligible_turn(binding, event, urgent=urgent):
            return LearningAdmission()
        source = self._source(binding, event, source_receipt, at)
        return self._admit(
            source,
            _AdmissionWrite(
                kind="turn",
                watermark=(
                    event.message_id
                    if event.event_kind is ConversationEventKind.MESSAGE_FINALIZED
                    else f"{event.message_id}:{event.revision}"
                ),
                event=event,
                experience=None,
                at=at,
                urgent=urgent,
                counted=event.event_kind is ConversationEventKind.MESSAGE_FINALIZED,
            ),
        )

    def admit_experience(
        self,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        source_receipt: IngestReceipt,
        experience: AgentExperienceRef,
        *,
        at: datetime,
    ) -> LearningAdmission:
        """Count one typed terminal foreground receipt without forging a user event."""
        if not self._eligible_experience(binding, event, experience):
            return LearningAdmission()
        source = self._source(binding, event, source_receipt, at)
        if experience.source_digest != source.source_digest:
            _fail("learning_experience_source_digest_conflict")
        return self._admit(
            source,
            _AdmissionWrite(
                kind="experience",
                watermark=experience.receipt_id,
                event=None,
                experience=experience,
                at=at,
                urgent=False,
                counted=True,
            ),
        )

    def counter(self, workspace_id: str) -> LearningCounter:
        with self.repository.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT counter_json FROM learning_counters WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()
            )
        if row is None:
            return LearningCounter(
                schema="knowledge.learning-counter.v1",
                workspace_id=workspace_id,
                updated_at=_EPOCH,
            )
        return LearningCounter.model_validate_json(row[0])

    def rounds(self, workspace_id: str) -> tuple[LearningRound, ...]:
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT round_json FROM learning_rounds
                    WHERE workspace_id=? ORDER BY created_at,round_id""",
                    (workspace_id,),
                ).fetchall()
            )
        return tuple(LearningRound.model_validate_json(row[0]) for row in rows)

    def partition_keys(self, round_id: str) -> tuple[str, ...]:
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT partition_key FROM learning_admissions
                    WHERE sealed_round_id=? AND invalidated=0 ORDER BY partition_key""",
                    (round_id,),
                ).fetchall()
            )
        return tuple(row[0] for row in rows)

    def ready_batches(self, round_id: str) -> tuple[str, ...]:
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT admission.batch_id
                    FROM learning_admissions AS admission
                    JOIN curation_batches AS batch USING(batch_id)
                    WHERE admission.sealed_round_id=? AND admission.invalidated=0
                        AND batch.state='ready' ORDER BY admission.batch_id""",
                    (round_id,),
                ).fetchall()
            )
        return tuple(row[0] for row in rows)

    def successful_experiences(self, round_id: str) -> tuple[AgentExperienceRef, ...]:
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT experience_json FROM learning_admissions
                    WHERE sealed_round_id=? AND admission_kind='experience'
                        AND invalidated=0 AND experience_json IS NOT NULL
                    ORDER BY rowid""",
                    (round_id,),
                ).fetchall()
            )
        experiences = tuple(AgentExperienceRef.model_validate_json(row[0]) for row in rows)
        reusable = {ExperienceOutcome.SUCCEEDED, ExperienceOutcome.OBSERVED}
        return tuple(item for item in experiences if item.outcome in reusable)

    def experiences_for_run(self, run_id: str) -> tuple[AgentExperienceRef, ...]:
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT experience_json FROM learning_admissions
                    WHERE run_id=? AND experience_json IS NOT NULL AND invalidated=0
                    ORDER BY rowid""",
                    (run_id,),
                ).fetchall()
            )
        return tuple(AgentExperienceRef.model_validate_json(row[0]) for row in rows)

    def source_run_id_for_job(self, job_id: str) -> str | None:
        """Resolve a learning job to its persisted foreground Run provenance."""
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT run_id FROM learning_admissions
                    WHERE job_id=? AND invalidated=0""",
                    (job_id,),
                ).fetchall()
            )
        if len(rows) > 1:
            _fail("learning_job_run_binding_conflict")
        return None if not rows else rows[0][0]

    def source_actor_for_job(self, job_id: str) -> ActorContext | None:
        """Return the exact actor persisted with a learning job admission."""
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT actor_json FROM learning_admissions
                    WHERE job_id=? AND invalidated=0""",
                    (job_id,),
                ).fetchall()
            )
        actors = tuple(ActorContext.model_validate_json(row[0]) for row in rows)
        if len(actors) > 1:
            _fail("learning_job_actor_binding_conflict")
        return None if not actors else actors[0]

    def invalidate_source(
        self,
        actor: ActorContext,
        *,
        source_id: str,
        source_revision_id: str,
    ) -> tuple[str, ...]:
        """Cancel rounds that retain a superseded or tombstoned source revision."""
        current = self.repository.read_source(actor, source_id)
        if current is not None and current.source.revision_id != source_revision_id:
            _fail("learning_source_revision_not_current")
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT sealed_round_id FROM learning_admissions
                    WHERE workspace_id=? AND source_id=? AND source_revision_id!=?
                        AND invalidated=0 AND sealed_round_id IS NOT NULL""",
                    (actor.workspace_id, source_id, source_revision_id),
                ).fetchall()
            )
            round_ids = tuple(row[0] for row in rows)
            _ = connection.execute(
                """UPDATE learning_admissions SET invalidated=1
                WHERE workspace_id=? AND source_id=? AND source_revision_id!=?""",
                (actor.workspace_id, source_id, source_revision_id),
            )
            for round_id in round_ids:
                self._set_round_state(connection, round_id, LearningRoundState.CANCELLED)
                self._cancel_round_batches(connection, round_id)
        return round_ids

    def consume_target(
        self,
        source: TrustedLearningSource,
        *,
        target_id: str,
        operation_id: str,
        at: datetime,
    ) -> bool:
        """Fence one foreground-applied target without consuming the whole source."""
        # A foreground result consumes provenance; it does not admit a background job.
        require_actor_event_binding(source.binding.actor, source.event)
        current = self.repository.canonical_event(source.binding.actor, source.event.message_id)
        stored = self.repository.read_source(source.binding.actor, source.receipt.source_id)
        if (
            current != source.event
            or stored is None
            or stored.source.revision_id != source.receipt.source_revision_id
        ):
            _fail("learning_source_revision_stale")
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """SELECT operation_id FROM learning_consumed_targets
                    WHERE workspace_id=? AND source_id=?
                        AND source_revision_id=? AND target_id=?""",
                    (
                        source.binding.actor.workspace_id,
                        source.receipt.source_id,
                        source.receipt.source_revision_id,
                        target_id,
                    ),
                ).fetchone()
            )
            if row is not None:
                if row[0] != operation_id:
                    _fail("learning_target_consumption_conflict")
                return False
            _ = connection.execute(
                """INSERT INTO learning_consumed_targets(
                    workspace_id,source_id,source_revision_id,target_id,operation_id,consumed_at
                ) VALUES (?,?,?,?,?,?)""",
                (
                    source.binding.actor.workspace_id,
                    source.receipt.source_id,
                    source.receipt.source_revision_id,
                    target_id,
                    operation_id,
                    at.isoformat(),
                ),
            )
        return True

    def review_for_job(self, job_id: str) -> LearningReviewRequest | None:
        """Project one partitioned sealed round into the existing curation request."""
        with self.repository.connection() as connection:
            anchor = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                connection.execute(
                    """SELECT sealed_round_id,partition_key FROM learning_admissions
                    WHERE job_id=? AND invalidated=0 AND sealed_round_id IS NOT NULL
                    ORDER BY rowid DESC LIMIT 1""",
                    (job_id,),
                ).fetchone()
            )
            if anchor is None:
                return None
            round_row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT round_json FROM learning_rounds WHERE round_id=?",
                    (anchor[0],),
                ).fetchone()
            )
            if round_row is None:
                return None
            round_ = LearningRound.model_validate_json(round_row[0])
            if round_.state is LearningRoundState.CANCELLED:
                return None
            rows = _REVIEW_ROWS.validate_python(
                connection.execute(
                    """SELECT event_json,experience_json,source_id,
                        source_revision_id,actor_json
                    FROM learning_admissions
                    WHERE sealed_round_id=? AND partition_key=? AND invalidated=0
                    ORDER BY rowid""",
                    (round_.round_id, anchor[1]),
                ).fetchall()
            )
            source_pairs = tuple(dict.fromkeys((row[2], row[3]) for row in rows))
            consumed_rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT target_id FROM learning_consumed_targets
                    WHERE workspace_id=? AND (source_id,source_revision_id) IN (
                        SELECT source_id,source_revision_id FROM learning_admissions
                        WHERE sealed_round_id=? AND partition_key=? AND invalidated=0
                    ) ORDER BY target_id""",
                    (round_.workspace_id, round_.round_id, anchor[1]),
                ).fetchall()
            )
        events = tuple(
            ConversationEvent.model_validate_json(row[0]) for row in rows if row[0] is not None
        )
        experiences = tuple(
            AgentExperienceRef.model_validate_json(row[1]) for row in rows if row[1] is not None
        )
        return LearningReviewRequest(
            schema="knowledge.learning-review-request.v1",
            round_id=round_.round_id,
            purpose=round_.purpose,
            user_event_refs=tuple(self._event_ref(event) for event in events),
            experience_refs=experiences,
            source_refs=tuple(
                EvidenceRef(
                    evidence_kind=EvidenceKind.SOURCE_SEGMENT,
                    evidence_id=source_id,
                    revision_id=revision_id,
                    scope=ActorContext.model_validate_json(rows[0][4]).conversation_scope,
                    instruction_authority=InstructionAuthority.DATA,
                    provenance=Provenance.AGENT_DERIVED,
                )
                for source_id, revision_id in source_pairs
            ),
            applicability=AppliesTo(),
            consumed_target_ids=tuple(row[0] for row in consumed_rows),
        )

    def _eligible_turn(
        self,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        *,
        urgent: bool,
    ) -> bool:
        return (
            event.role is ConversationRole.USER
            and (
                event.event_kind is ConversationEventKind.MESSAGE_FINALIZED
                or (urgent and event.event_kind is ConversationEventKind.MESSAGE_EDITED)
            )
            and event.scope.kind is ScopeKind.WORKSPACE
            and not binding.run_id.startswith("background.")
        )

    def _eligible_experience(
        self,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        experience: AgentExperienceRef,
    ) -> bool:
        return (
            event.scope.kind is ScopeKind.WORKSPACE
            and experience.workspace_id == binding.actor.workspace_id
            and experience.run_id == binding.run_id
            and not binding.run_id.startswith("background.")
            and not experience.capability_id.startswith(("knowledge_", "memory_", "skill_"))
        )

    def _source(
        self,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        receipt: IngestReceipt,
        at: datetime,
    ) -> _AdmissionSource:
        require_actor_event_binding(binding.actor, event)
        if binding.actor.workspace_id != event.scope.workspace_id:
            _fail("learning_binding_workspace_conflict")
        stored = self.repository.read_source(binding.actor, receipt.source_id)
        if stored is None or stored.source.revision_id != receipt.source_revision_id:
            _fail("learning_source_revision_stale")
        with self.repository.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT job_json FROM jobs WHERE job_id=?",
                    (receipt.curation_job_id,),
                ).fetchone()
            )
        if row is None:
            _fail("learning_source_job_missing")
        job = KnowledgeJob.model_validate_json(row[0])
        if (
            job.workspace_id != binding.actor.workspace_id
            or job.scope != event.scope
            or job.policy_version != LEARNING_POLICY_VERSION
        ):
            _fail("learning_source_job_binding_conflict")
        item = CurationBatchItem(
            job_id=job.job_id,
            event_id=job.root_event_id,
            event_revision=event.revision,
            actor=binding.actor.model_copy(update={"authenticated_at": at}),
            policy_version=job.policy_version,
            priority=job.priority,
            occurred_at=at,
        )
        partition_key = curation_partition_key(item)
        batch = self._collect(item, job)
        return _AdmissionSource(
            actor=item.actor,
            run_id=binding.run_id,
            event=event,
            receipt=receipt,
            source_digest=stored.source.sha256,
            job=job,
            batch=batch,
            partition_key=partition_key,
        )

    def _collect(self, item: CurationBatchItem, job: KnowledgeJob) -> CurationBatch:
        if job.state is JobState.QUEUED:
            return BatchCurationCoordinator(self.repository).collect(item)
        if job.batch_id is None:
            _fail("learning_source_job_not_collectable")
        batch = self.repository.curation_batch(item.actor, job.batch_id)
        if batch is None:
            _fail("learning_source_batch_missing")
        return batch

    def _admit(
        self,
        source: _AdmissionSource,
        write: _AdmissionWrite,
    ) -> LearningAdmission:
        admission_id = stable_id(
            "learning-admission",
            source.actor.workspace_id,
            write.kind,
            write.watermark,
        )
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            existing = _OPTIONAL_NULLABLE_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT sealed_round_id FROM learning_admissions WHERE admission_id=?",
                    (admission_id,),
                ).fetchone()
            )
            if existing is not None:
                return LearningAdmission()
            counter = self._counter_in(connection, source.actor.workspace_id, write.at)
            turns = counter.conversation_turns + (
                1 if write.kind == "turn" and write.counted else 0
            )
            receipts = counter.terminal_tool_receipts + (
                1 if write.kind == "experience" and write.counted else 0
            )
            _ = connection.execute(
                """INSERT INTO learning_admissions(
                    admission_id,workspace_id,admission_kind,watermark,run_id,source_id,
                    source_revision_id,job_id,partition_key,batch_id,actor_json,event_json,
                    experience_json,occurred_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    admission_id,
                    source.actor.workspace_id,
                    write.kind,
                    write.watermark,
                    source.run_id,
                    source.receipt.source_id,
                    source.receipt.source_revision_id,
                    source.job.job_id,
                    source.partition_key,
                    source.batch.batch_id,
                    source.actor.model_dump_json(),
                    None if write.event is None else write.event.model_dump_json(),
                    (None if write.experience is None else write.experience.model_dump_json()),
                    write.at.isoformat(),
                ),
            )
            should_seal = write.urgent or turns >= _THRESHOLD or receipts >= _THRESHOLD
            if not should_seal:
                self._put_counter(
                    connection,
                    counter.model_copy(
                        update={
                            "conversation_turns": turns,
                            "terminal_tool_receipts": receipts,
                            "turn_watermark": (
                                write.watermark if write.kind == "turn" else counter.turn_watermark
                            ),
                            "receipt_watermark": (
                                write.watermark
                                if write.kind == "experience"
                                else counter.receipt_watermark
                            ),
                            "updated_at": write.at,
                        }
                    ),
                )
                return LearningAdmission()
            purpose = (
                LearningPurpose.CONVERSATIONAL_FEEDBACK
                if write.kind == "turn"
                else LearningPurpose.TERMINAL_EXPERIENCE_REVIEW
            )
            return self._seal(
                connection,
                counter,
                _SealWindow(
                    purpose=purpose,
                    at=write.at,
                    only_admission_id=None if write.counted else admission_id,
                    preserve_counter=not write.counted,
                ),
            )

    def _seal(
        self,
        connection: sqlite3.Connection,
        counter: LearningCounter,
        window: _SealWindow,
    ) -> LearningAdmission:
        rows = _SEAL_ROWS.validate_python(
            connection.execute(
                """SELECT admission_id,admission_kind,watermark,experience_json,batch_id
                FROM learning_admissions
                WHERE workspace_id=? AND sealed_round_id IS NULL AND invalidated=0
                    AND (? IS NULL OR admission_id=?)
                ORDER BY rowid""",
                (
                    counter.workspace_id,
                    window.only_admission_id,
                    window.only_admission_id,
                ),
            ).fetchall()
        )
        turn_rows = tuple(row for row in rows if row[1] == "turn")
        receipt_rows = tuple(row for row in rows if row[1] == "experience")
        experiences = tuple(
            AgentExperienceRef.model_validate_json(row[3])
            for row in receipt_rows
            if row[3] is not None
        )
        end_turn = None if not turn_rows else turn_rows[-1][2]
        end_receipt = None if not receipt_rows else receipt_rows[-1][2]
        round_id = stable_id(
            "learning-round",
            counter.workspace_id,
            window.purpose.value,
            end_turn or "",
            end_receipt or "",
        )
        previous_round_row = (
            None
            if counter.round_id is None
            else _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT round_json FROM learning_rounds WHERE round_id=?",
                    (counter.round_id,),
                ).fetchone()
            )
        )
        previous_round = (
            None
            if previous_round_row is None
            else LearningRound.model_validate_json(previous_round_row[0])
        )
        round_ = LearningRound(
            schema="knowledge.learning-round.v1",
            round_id=round_id,
            workspace_id=counter.workspace_id,
            purpose=window.purpose,
            start_turn_watermark=(
                None if previous_round is None else previous_round.end_turn_watermark
            ),
            start_receipt_watermark=(
                None if previous_round is None else previous_round.end_receipt_watermark
            ),
            end_turn_watermark=end_turn,
            end_receipt_watermark=end_receipt,
            turn_count=len(turn_rows),
            receipt_count=len(receipt_rows),
            experience_refs=experiences,
            state=LearningRoundState.READY,
            created_at=window.at,
        )
        _ = connection.execute(
            """INSERT INTO learning_rounds(
                round_id,workspace_id,purpose,state,created_at,round_json
            ) VALUES (?,?,?,?,?,?)""",
            (
                round_id,
                round_.workspace_id,
                round_.purpose.value,
                round_.state.value,
                window.at.isoformat(),
                round_.model_dump_json(),
            ),
        )
        _ = connection.execute(
            """UPDATE learning_admissions SET sealed_round_id=?
            WHERE workspace_id=? AND sealed_round_id IS NULL AND invalidated=0
                AND (? IS NULL OR admission_id=?)""",
            (
                round_id,
                counter.workspace_id,
                window.only_admission_id,
                window.only_admission_id,
            ),
        )
        batch_ids = tuple(dict.fromkeys(row[4] for row in rows))
        for batch_id in batch_ids:
            self._ready_batch(connection, batch_id)
        if not window.preserve_counter:
            self._put_counter(
                connection,
                counter.model_copy(
                    update={
                        "conversation_turns": 0,
                        "terminal_tool_receipts": 0,
                        "turn_watermark": end_turn or counter.turn_watermark,
                        "receipt_watermark": end_receipt or counter.receipt_watermark,
                        "round_id": round_id,
                        "updated_at": window.at,
                    }
                ),
            )
        return LearningAdmission(ready_batch_ids=batch_ids)

    def _counter_in(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        at: datetime,
    ) -> LearningCounter:
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                "SELECT counter_json FROM learning_counters WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        )
        if row is None:
            return LearningCounter(
                schema="knowledge.learning-counter.v1",
                workspace_id=workspace_id,
                updated_at=at,
            )
        return LearningCounter.model_validate_json(row[0])

    @staticmethod
    def _put_counter(connection: sqlite3.Connection, counter: LearningCounter) -> None:
        _ = connection.execute(
            """INSERT INTO learning_counters(workspace_id,counter_json) VALUES (?,?)
            ON CONFLICT(workspace_id) DO UPDATE SET counter_json=excluded.counter_json""",
            (counter.workspace_id, counter.model_dump_json()),
        )

    @staticmethod
    def _ready_batch(connection: sqlite3.Connection, batch_id: str) -> None:
        row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
            connection.execute(
                "SELECT batch_json,state FROM curation_batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
        )
        if row is None:
            _fail("learning_batch_missing")
        batch = CurationBatch.model_validate_json(row[0])
        if batch.state is BatchState.COLLECTING:
            ready = batch.model_copy(update={"state": BatchState.READY})
            _ = connection.execute(
                """UPDATE curation_batches SET state='ready',batch_json=?
                WHERE batch_id=? AND state='collecting'""",
                (ready.model_dump_json(), batch_id),
            )

    def _set_round_state(
        self,
        connection: sqlite3.Connection,
        round_id: str,
        state: LearningRoundState,
    ) -> None:
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                "SELECT round_json FROM learning_rounds WHERE round_id=?",
                (round_id,),
            ).fetchone()
        )
        if row is None:
            return
        round_ = LearningRound.model_validate_json(row[0]).model_copy(update={"state": state})
        _ = connection.execute(
            "UPDATE learning_rounds SET state=?,round_json=? WHERE round_id=?",
            (state.value, round_.model_dump_json(), round_id),
        )

    def _cancel_round_batches(self, connection: sqlite3.Connection, round_id: str) -> None:
        rows = _STRING_ROWS.validate_python(
            connection.execute(
                "SELECT DISTINCT batch_id FROM learning_admissions WHERE sealed_round_id=?",
                (round_id,),
            ).fetchall()
        )
        for (batch_id,) in rows:
            batch_row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                connection.execute(
                    "SELECT batch_json,state FROM curation_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
            )
            if batch_row is None or batch_row[1] not in {"collecting", "ready"}:
                continue
            batch = CurationBatch.model_validate_json(batch_row[0]).model_copy(
                update={"state": BatchState.CANCELLED}
            )
            _ = connection.execute(
                "UPDATE curation_batches SET state='cancelled',batch_json=? WHERE batch_id=?",
                (batch.model_dump_json(), batch_id),
            )
            _ = connection.execute(
                """UPDATE jobs SET state='cancelled',reason_code='learning_source_invalidated',
                    job_json=json_set(job_json,'$.state','cancelled',
                        '$.reason_code','learning_source_invalidated')
                WHERE batch_id=? AND state IN ('waiting_dependency','queued')""",
                (batch_id,),
            )

    @staticmethod
    def _event_ref(event: ConversationEvent) -> EvidenceRef:
        return EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id=event.message_id,
            revision_id=str(event.revision),
            quote_sha256=sha256(event.text.encode()).hexdigest(),
            scope=event.scope,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        )


def _fail(code: str) -> Never:
    raise LearningRepositoryError(code)


__all__ = [
    "LearningAdmission",
    "LearningRepositoryError",
    "LearningReviewCoordinator",
]
