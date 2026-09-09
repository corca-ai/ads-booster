from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Never, override

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import ContextReceipt
from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
from ads_booster.knowledge.contract_types import (
    MemoryKind,
    ScopeKind,
    SourceDisposition,
    SourceKind,
)
from ads_booster.knowledge.file_paths import SourceFileKind, SourceRevisionTarget
from ads_booster.knowledge.governance_contracts import TaskBinding, TaskOverlay
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryEntry
from ads_booster.knowledge.messages import require_actor_event_binding
from ads_booster.knowledge.operation_contracts import KnowledgeJob, MemoryExplanation
from ads_booster.knowledge.operation_enums import TaskBindingState
from ads_booster.knowledge.repository_conversation_deletion import READABLE_CONVERSATION_EVENT
from ads_booster.knowledge.repository_source import _insert_job, _require_read
from ads_booster.knowledge.repository_types import (
    IndexOutboxItem,
    JobRegistration,
    SourceAdmissionChange,
    StoredSource,
)
from ads_booster.knowledge.scope_contracts import AccessScope
from ads_booster.knowledge.source_contracts import ConversationEvent, Source, SourceSegment
from ads_booster.knowledge.tool_contracts import (
    ProposalTargetKind,
    QuestionRecord,
    QuestionStatus,
    ScheduledKnowledgeRequest,
)
from ads_booster.knowledge.wiki_contracts import Claim, WikiPage

if TYPE_CHECKING:
    import sqlite3
    from datetime import date

    from ads_booster.knowledge.repository_protocol import KnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.transport.json_types import JsonObject

_STRING: TypeAdapter[str] = TypeAdapter(str)
_OPTIONAL_INTEGER_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_OPTIONAL_STRING_PAIR_ROW: TypeAdapter[tuple[str, str] | None] = TypeAdapter(tuple[str, str] | None)
_OPTIONAL_STRING_TRIPLE_ROW: TypeAdapter[tuple[str, str, str] | None] = TypeAdapter(
    tuple[str, str, str] | None
)
_OPTIONAL_STRING_QUAD_ROW: TypeAdapter[tuple[str, str, str, str] | None] = TypeAdapter(
    tuple[str, str, str, str] | None
)
_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


@dataclass(slots=True)
class ToolStateError(Exception):
    code: str
    target: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


@dataclass(frozen=True, slots=True)
class CorrectionTarget:
    target_kind: ProposalTargetKind
    target_id: str
    expected_revision_id: str
    brand_id: str | None
    scope: AccessScope


@dataclass(frozen=True, slots=True)
class RepositoryToolState:
    repository: KnowledgeRepository

    def open_task(self, actor: ActorContext, binding: TaskBinding) -> TaskBinding:
        self._require_task_actor(actor, binding)
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT binding_json FROM task_bindings WHERE workspace_id=? AND task_id=?",
                    (actor.workspace_id, binding.task_id),
                ).fetchone()
            )
            if row is not None:
                current = binding.__class__.model_validate_json(_STRING.validate_python(row[0]))
                if current == binding:
                    return current
                _fail("task_idempotency_conflict", binding.task_id)
            _ = connection.execute(
                """
                INSERT INTO task_bindings(
                    workspace_id,task_id,actor_ref,member_id,session_id,action_kind,
                    brand_id,brand_catalog_revision,capability_epoch,state,opened_at,closed_at,
                    binding_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    binding.workspace_id,
                    binding.task_id,
                    binding.actor_ref,
                    binding.member_id,
                    binding.session_id,
                    binding.action_kind.value,
                    binding.brand_id,
                    binding.brand_catalog_revision,
                    binding.capability_epoch,
                    binding.state.value,
                    binding.opened_at.isoformat(),
                    None,
                    binding.model_dump_json(),
                ),
            )
        return binding

    def close_task(
        self,
        actor: ActorContext,
        task_id: str,
        capability_epoch: int,
        closed_at: datetime,
    ) -> TaskBinding:
        current = self.task_binding(actor, task_id)
        if current is None:
            _fail("task_not_found", task_id)
        if current.actor_ref != actor.actor_id or current.capability_epoch != capability_epoch:
            _fail("task_scope_mismatch", task_id)
        if current.state.value == "closed":
            return current
        closed = current.model_copy(
            update={"state": TaskBindingState.CLOSED, "closed_at": closed_at}
        )
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE task_bindings SET state='closed',closed_at=?,binding_json=?
                WHERE workspace_id=? AND task_id=? AND state='active' AND capability_epoch=?
                """,
                (
                    closed_at.isoformat(),
                    closed.model_dump_json(),
                    actor.workspace_id,
                    task_id,
                    capability_epoch,
                ),
            )
            if cursor.rowcount != 1:
                _fail("task_scope_mismatch", task_id)
        return closed

    def task_binding(self, actor: ActorContext, task_id: str) -> TaskBinding | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT binding_json FROM task_bindings WHERE workspace_id=? AND task_id=?",
                    (actor.workspace_id, task_id),
                ).fetchone()
            )
        return (
            None
            if row is None
            else TaskBinding.model_validate_json(_STRING.validate_python(row[0]))
        )

    def put_task_overlay(
        self,
        actor: ActorContext,
        overlay: TaskOverlay,
    ) -> tuple[TaskOverlay, bool]:
        binding = self.task_binding(actor, overlay.task_id)
        if binding is None:
            _fail("task_not_found", overlay.task_id)
        if (
            binding.state.value != "active"
            or binding.actor_ref != actor.actor_id
            or binding.workspace_id != overlay.workspace_id
            or binding.actor_ref != overlay.actor_ref
            or binding.capability_epoch != overlay.capability_epoch
            or binding.brand_id != overlay.brand_id
        ):
            _fail("task_scope_mismatch", overlay.task_id)
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT overlay_json FROM task_overlays WHERE workspace_id=? AND overlay_id=?",
                    (actor.workspace_id, overlay.overlay_id),
                ).fetchone()
            )
            if row is not None:
                current = overlay.__class__.model_validate_json(_STRING.validate_python(row[0]))
                if current == overlay:
                    return current, True
                _fail("task_overlay_idempotency_conflict", overlay.overlay_id)
            _ = connection.execute(
                """
                INSERT INTO task_overlays(
                    workspace_id,overlay_id,task_id,actor_ref,capability_epoch,brand_id,
                    overlay_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    overlay.workspace_id,
                    overlay.overlay_id,
                    overlay.task_id,
                    overlay.actor_ref,
                    overlay.capability_epoch,
                    overlay.brand_id,
                    overlay.model_dump_json(),
                    overlay.created_at.isoformat(),
                ),
            )
        return overlay, False

    def active_task_overlays(self, actor: ActorContext, task_id: str) -> tuple[TaskOverlay, ...]:
        binding = self.task_binding(actor, task_id)
        if binding is None or binding.state.value != "active":
            return ()
        if binding.actor_ref != actor.actor_id or binding.capability_epoch != actor.policy_epoch:
            _fail("task_scope_mismatch", task_id)
        with self.repository.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """
                    SELECT overlay.overlay_json FROM task_overlays AS overlay
                    JOIN task_bindings AS binding USING(workspace_id,task_id)
                    WHERE overlay.workspace_id=? AND overlay.task_id=? AND binding.state='active'
                    AND overlay.actor_ref=? AND overlay.capability_epoch=?
                    ORDER BY overlay.created_at
                    """,
                    (actor.workspace_id, task_id, actor.actor_id, actor.policy_epoch),
                ).fetchall()
            )
        return tuple(
            TaskOverlay.model_validate_json(_STRING.validate_python(row[0])) for row in rows
        )

    def canonical_event(self, actor: ActorContext, event_ref: str) -> ConversationEvent:
        event = self.read_canonical_event(actor, event_ref)
        require_actor_event_binding(actor, event)
        return event

    def read_canonical_event(self, actor: ActorContext, event_ref: str) -> ConversationEvent:
        """Read admitted evidence without treating the reader as its author."""
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    f"""
                    SELECT event_json FROM conversation_events AS event
                    WHERE event.workspace_id=? AND event.message_id=?
                    AND {READABLE_CONVERSATION_EVENT}
                    ORDER BY event.revision DESC LIMIT 1
                    """,  # noqa: S608 - static SQL predicate; all input values are bound
                    (actor.workspace_id, event_ref),
                ).fetchone()
            )
        if row is None:
            _fail("authenticated_event_not_found", event_ref)
        event = ConversationEvent.model_validate_json(_STRING.validate_python(row[0]))
        _ = authorize_read(actor=actor, target_scope=event.scope, at=datetime.now(UTC))
        return event

    def mark_event_source_use_only(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        operation_id: str,
        occurred_at: datetime,
    ) -> bool:
        identity = f"message:{event.conversation_id}:{event.message_id}"
        source = self.repository.source_by_identity(actor, SourceKind.MESSAGE, identity)
        if source is None:
            _fail("correction_source_not_found", event.message_id)
        if source.disposition is SourceDisposition.USE_ONLY:
            return True
        _ = authorize_write(actor=actor, target_scope=source.scope, at=occurred_at)
        payload: JsonObject = {
            "source_id": source.source_id,
            "disposition": SourceDisposition.USE_ONLY.value,
            "event_id": event.message_id,
        }
        updated = self.repository.change_source_admission(
            SourceAdmissionChange(
                operation_id=f"{operation_id}.use-only",
                payload_sha256=contract_sha256(payload),
                workspace_id=actor.workspace_id,
                source_id=source.source_id,
                expected_admission_revision=source.admission_revision,
                disposition=SourceDisposition.USE_ONLY,
                index_item=IndexOutboxItem(
                    item_id=f"index.{operation_id}.use-only",
                    workspace_id=actor.workspace_id,
                    entity_kind="source",
                    entity_id=source.source_id,
                    revision_id=source.revision_id,
                    extraction_version=source.extractor_version,
                    admission_revision=source.admission_revision + 1,
                ),
                occurred_at=occurred_at,
            )
        )
        return updated.disposition is SourceDisposition.USE_ONLY

    def find_memory_document_id(
        self,
        actor: ActorContext,
        kind: MemoryKind,
        brand_id: str | None,
        local_date: date | None,
    ) -> str | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT document_id FROM memory_documents
                    WHERE workspace_id=? AND kind=? AND brand_id IS ? AND local_date IS ?
                    """,
                    (
                        actor.workspace_id,
                        kind.value,
                        brand_id,
                        None if local_date is None else local_date.isoformat(),
                    ),
                ).fetchone()
            )
        return None if row is None else _STRING.validate_python(row[0])

    def correction_target(self, actor: ActorContext, target_id: str) -> CorrectionTarget:
        memory = self._memory_correction_target(actor, target_id)
        if memory is not None:
            return memory
        document = self._memory_document_correction_target(actor, target_id)
        if document is not None:
            return document
        page = self._page_correction_target(actor, target_id)
        if page is not None:
            return page
        return _fail("correction_target_not_found", target_id)

    def explain(
        self,
        actor: ActorContext,
        target_id: str,
        task_receipt_id: str | None,
    ) -> MemoryExplanation | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            memory_row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                connection.execute(
                    """
                    SELECT entry.entry_json,scope.scope_json FROM memory_entries AS entry
                    JOIN memory_heads AS head ON head.workspace_id=entry.workspace_id
                        AND head.document_id=entry.document_id
                        AND head.revision_id=entry.memory_revision_id
                    JOIN access_scopes AS scope ON scope.scope_key=entry.scope_key
                    WHERE entry.workspace_id=? AND entry.entry_id=?
                    """,
                    (actor.workspace_id, target_id),
                ).fetchone()
            )
            claim_row: tuple[str, str] | None = None
            if memory_row is None:
                claim_row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                    connection.execute(
                        """
                        SELECT version.claim_json,page.page_json FROM claim_versions AS version
                        JOIN claim_locations AS location
                            ON location.workspace_id=version.workspace_id
                            AND location.claim_id=version.claim_id
                            AND location.claim_revision_id=version.revision_id
                        JOIN wiki_pages AS page ON page.workspace_id=location.workspace_id
                            AND page.page_id=location.page_id
                        WHERE version.workspace_id=? AND version.claim_id=?
                            AND location.is_current=1
                        """,
                        (actor.workspace_id, target_id),
                    ).fetchone()
                )
            receipt_row = (
                None
                if task_receipt_id is None
                else _OPTIONAL_STRING_ROW.validate_python(
                    connection.execute(
                        """
                        SELECT receipt_json FROM context_receipts
                        WHERE workspace_id=? AND receipt_id=? AND actor_ref=?
                        """,
                        (actor.workspace_id, task_receipt_id, actor.actor_id),
                    ).fetchone()
                )
            )
        if memory_row is not None:
            entry = MemoryEntry.model_validate_json(_STRING.validate_python(memory_row[0]))
            scope = AccessScope.model_validate_json(_STRING.validate_python(memory_row[1]))
            _ = authorize_read(actor=actor, target_scope=scope, at=datetime.now(UTC))
            evidence_ids = tuple(
                ref.evidence_id
                for ref in entry.source_refs
                if self._may_disclose_scope(actor, ref.scope)
            )
            admission_reason = entry.admission_reason
            applicability = (
                "" if entry.applicability is None else entry.applicability.model_dump_json()
            )
        elif claim_row is not None:
            claim = Claim.model_validate_json(_STRING.validate_python(claim_row[0]))
            page = WikiPage.model_validate_json(_STRING.validate_python(claim_row[1]))
            _ = authorize_read(actor=actor, target_scope=page.scope, at=datetime.now(UTC))
            evidence_ids = tuple(
                ref.evidence_id
                for ref in (*claim.evidence_refs, *claim.counter_evidence_refs)
                if self._may_disclose_scope(actor, ref.scope)
            )
            admission_reason = claim.admission_reason
            applicability = (
                "" if claim.applicability is None else claim.applicability.model_dump_json()
            )
        else:
            return None
        selected_reason = self._selection_reason(receipt_row, target_id, task_receipt_id)
        return MemoryExplanation(
            explanation_id=f"explain.{target_id}",
            target_id=target_id,
            admission_reason=admission_reason,
            evidence_refs=evidence_ids,
            applicability=applicability,
            selected_reason=selected_reason,
            unknown_reason=task_receipt_id is not None and selected_reason is None,
        )

    def put_question(
        self, actor: ActorContext, question: QuestionRecord
    ) -> tuple[QuestionRecord, bool]:
        if question.workspace_id != actor.workspace_id or question.actor_ref != actor.actor_id:
            _fail("question_actor_mismatch", question.question_id)
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT question_json FROM knowledge_questions
                    WHERE workspace_id=? AND question_id=?
                    """,
                    (actor.workspace_id, question.question_id),
                ).fetchone()
            )
            if row is not None:
                current = QuestionRecord.model_validate_json(_STRING.validate_python(row[0]))
                if current == question:
                    return current, True
                _fail("question_idempotency_conflict", question.question_id)
            _ = connection.execute(
                """
                INSERT INTO knowledge_questions(
                    workspace_id,question_id,actor_ref,status,question_json,answer_event_id,
                    created_at,answered_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    question.workspace_id,
                    question.question_id,
                    question.actor_ref,
                    question.status.value,
                    question.model_dump_json(),
                    None,
                    question.created_at.isoformat(),
                    None,
                ),
            )
        return question, False

    def question(self, actor: ActorContext, question_id: str) -> QuestionRecord | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT question_json FROM knowledge_questions
                    WHERE workspace_id=? AND question_id=? AND actor_ref=?
                    """,
                    (actor.workspace_id, question_id, actor.actor_id),
                ).fetchone()
            )
        return (
            None
            if row is None
            else QuestionRecord.model_validate_json(_STRING.validate_python(row[0]))
        )

    def answer_question(
        self,
        actor: ActorContext,
        answered: QuestionRecord,
        receipt: ExplicitAdoptionReceipt | None,
    ) -> tuple[QuestionRecord, ExplicitAdoptionReceipt | None, bool]:
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT question_json FROM knowledge_questions
                    WHERE workspace_id=? AND question_id=? AND actor_ref=?
                    """,
                    (actor.workspace_id, answered.question_id, actor.actor_id),
                ).fetchone()
            )
            if row is None:
                _fail("question_not_found", answered.question_id)
            current = QuestionRecord.model_validate_json(_STRING.validate_python(row[0]))
            if current.status is QuestionStatus.ANSWERED:
                if current != answered:
                    _fail("question_answer_conflict", answered.question_id)
                stored_receipt = self._adoption_receipt_in(connection, actor, current)
                return current, stored_receipt, True
            if current.status is not QuestionStatus.PENDING:
                _fail("question_not_pending", answered.question_id)
            cursor = connection.execute(
                """
                UPDATE knowledge_questions SET status='answered',question_json=?,answer_event_id=?,
                    answered_at=? WHERE workspace_id=? AND question_id=? AND status='pending'
                """,
                (
                    answered.model_dump_json(),
                    answered.answer_event_id,
                    answered.answered_at.isoformat() if answered.answered_at is not None else None,
                    actor.workspace_id,
                    answered.question_id,
                ),
            )
            if cursor.rowcount != 1:
                _fail("question_answer_conflict", answered.question_id)
            if receipt is not None:
                _ = connection.execute(
                    """
                    INSERT INTO explicit_adoption_receipts(
                        workspace_id,receipt_id,question_id,proposal_id,brand_id,
                        expected_revision_id,authenticated_event_id,receipt_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        receipt.workspace_id,
                        receipt.receipt_id,
                        receipt.question_id,
                        receipt.proposal_id,
                        receipt.brand_id,
                        receipt.expected_revision_id,
                        receipt.authenticated_event_id,
                        receipt.model_dump_json(),
                        receipt.answered_at.isoformat(),
                    ),
                )
        return answered, receipt, False

    def adoption_receipts(
        self,
        actor: ActorContext,
        receipt_ids: tuple[str, ...],
    ) -> tuple[ExplicitAdoptionReceipt, ...]:
        if not receipt_ids:
            return ()
        results: list[ExplicitAdoptionReceipt] = []
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            for receipt_id in receipt_ids:
                row = _OPTIONAL_STRING_ROW.validate_python(
                    connection.execute(
                        """
                        SELECT receipt_json FROM explicit_adoption_receipts
                        WHERE workspace_id=? AND receipt_id=?
                        """,
                        (actor.workspace_id, receipt_id),
                    ).fetchone()
                )
                if row is None:
                    _fail("adoption_receipt_not_found", receipt_id)
                results.append(
                    ExplicitAdoptionReceipt.model_validate_json(_STRING.validate_python(row[0]))
                )
        return tuple(results)

    def adoption_receipt(
        self,
        actor: ActorContext,
        receipt_id: str,
    ) -> ExplicitAdoptionReceipt | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT receipt_json FROM explicit_adoption_receipts
                    WHERE workspace_id=? AND receipt_id=?
                    """,
                    (actor.workspace_id, receipt_id),
                ).fetchone()
            )
        return (
            None
            if row is None
            else ExplicitAdoptionReceipt.model_validate_json(_STRING.validate_python(row[0]))
        )

    def job_exists(self, actor: ActorContext, job_id: str) -> bool:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    "SELECT 1 FROM jobs WHERE workspace_id=? AND job_id=?",
                    (actor.workspace_id, job_id),
                ).fetchone()
            )
        return row is not None

    def page_title_alias_matches(self, actor: ActorContext, page_id: str, name: str) -> bool:
        stored = self.repository.read_page(actor, page_id)
        if stored is None:
            return False
        normalized = name.casefold()
        return stored.page.title.casefold() == normalized or any(
            alias.casefold() == normalized for alias in stored.page.aliases
        )

    def reference_exists(self, actor: ActorContext, reference_id: str) -> bool:
        if self.repository.read_source(actor, reference_id) is not None:
            return True
        if self.repository.read_page(actor, reference_id) is not None:
            return True
        try:
            _ = self.correction_target(actor, reference_id)
        except ToolStateError as error:
            if error.code != "correction_target_not_found":
                raise
        else:
            return True
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """
                    SELECT 1 FROM context_receipts WHERE workspace_id=? AND receipt_id=?
                    UNION ALL SELECT 1 FROM operations WHERE workspace_id=? AND operation_id=?
                    UNION ALL SELECT 1 FROM jobs WHERE workspace_id=? AND job_id=?
                    LIMIT 1
                    """,
                    (
                        actor.workspace_id,
                        reference_id,
                        actor.workspace_id,
                        reference_id,
                        actor.workspace_id,
                        reference_id,
                    ),
                ).fetchone()
            )
        if row is not None:
            return True
        try:
            _ = self.read_canonical_event(actor, reference_id)
        except ToolStateError as error:
            if error.code == "authenticated_event_not_found":
                return False
            raise
        return True

    def put_scheduled_job(
        self,
        actor: ActorContext,
        registration: JobRegistration,
        request: ScheduledKnowledgeRequest,
    ) -> bool:
        if (
            registration.job.workspace_id != actor.workspace_id
            or request.workspace_id != actor.workspace_id
            or request.job_id != registration.job.job_id
        ):
            _fail("scheduled_job_binding_mismatch", request.job_id)
        with self.repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                connection.execute(
                    """
                    SELECT job.unique_key,request.request_json FROM jobs AS job
                    JOIN knowledge_job_requests AS request USING(job_id)
                    WHERE job.workspace_id=? AND job.job_id=?
                    """,
                    (actor.workspace_id, request.job_id),
                ).fetchone()
            )
            if row is not None:
                stored = ScheduledKnowledgeRequest.model_validate_json(
                    _STRING.validate_python(row[1])
                )
                if _STRING.validate_python(row[0]) == registration.unique_key and stored == request:
                    return True
                _fail("job_idempotency_conflict", request.job_id)
            _insert_job(connection, registration.job, registration.unique_key)
            _ = connection.execute(
                """
                INSERT INTO knowledge_job_requests(
                    job_id,workspace_id,operation_id,payload_sha256,request_json
                ) VALUES (?,?,?,?,?)
                """,
                (
                    request.job_id,
                    request.workspace_id,
                    request.operation_id,
                    contract_sha256(request),
                    request.model_dump_json(),
                ),
            )
        return False

    def scheduled_job_request(
        self,
        actor: ActorContext,
        job_id: str,
    ) -> ScheduledKnowledgeRequest | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_PAIR_ROW.validate_python(
                connection.execute(
                    """
                    SELECT request.request_json,job.job_json FROM knowledge_job_requests AS request
                    JOIN jobs AS job USING(job_id)
                    WHERE request.workspace_id=? AND request.job_id=?
                    """,
                    (actor.workspace_id, job_id),
                ).fetchone()
            )
        if row is None:
            return None
        job = KnowledgeJob.model_validate_json(row[1])
        _ = authorize_read(actor=actor, target_scope=job.scope, at=datetime.now(UTC))
        return ScheduledKnowledgeRequest.model_validate_json(row[0])

    def read_source_extract(
        self,
        actor: ActorContext,
        source_id: str,
        revision_id: str | None,
    ) -> StoredSource | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            selected = revision_id
            if selected is None:
                head = _OPTIONAL_STRING_ROW.validate_python(
                    connection.execute(
                        "SELECT revision_id FROM source_heads WHERE workspace_id=? AND source_id=?",
                        (actor.workspace_id, source_id),
                    ).fetchone()
                )
                if head is None:
                    return None
                selected = _STRING.validate_python(head[0])
            row = _OPTIONAL_STRING_TRIPLE_ROW.validate_python(
                connection.execute(
                    """
                    SELECT revision.revision_json,source.source_json,file.sha256
                    FROM source_revisions AS revision
                    JOIN sources AS source USING(workspace_id,source_id)
                    JOIN source_files AS file USING(workspace_id,source_id,revision_id)
                    WHERE revision.workspace_id=? AND revision.source_id=?
                        AND revision.revision_id=? AND file.file_kind='extracted.md'
                        AND source.visibility!='blocked'
                    """,
                    (actor.workspace_id, source_id, selected),
                ).fetchone()
            )
            if row is None:
                return None
            revision_source = Source.model_validate_json(_STRING.validate_python(row[0]))
            current_source = Source.model_validate_json(_STRING.validate_python(row[1]))
            source = revision_source.model_copy(
                update={
                    "disposition": current_source.disposition,
                    "admission_revision": current_source.admission_revision,
                }
            )
            _ = authorize_read(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
            segment_rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """
                    SELECT segment_json FROM segments
                    WHERE workspace_id=? AND source_id=? AND revision_id=?
                    ORDER BY extraction_version,segment_id
                    """,
                    (actor.workspace_id, source_id, selected),
                ).fetchall()
            )
        target = SourceRevisionTarget(
            source_id=source_id,
            revision_id=selected,
            file_kind=SourceFileKind.EXTRACTED,
        )
        published = self.repository.files.published(target, _STRING.validate_python(row[2]))
        return StoredSource(
            source=source,
            segments=tuple(
                SourceSegment.model_validate_json(_STRING.validate_python(item[0]))
                for item in segment_rows
            ),
            body=self.repository.files.read(published),
        )

    def _memory_correction_target(
        self,
        actor: ActorContext,
        target_id: str,
    ) -> CorrectionTarget | None:
        with self.repository.connection() as connection:
            _require_read(connection, actor)
            row = _OPTIONAL_STRING_QUAD_ROW.validate_python(
                connection.execute(
                    """
                    SELECT entry.entry_json,document.document_json,
                        head.revision_id,scope.scope_json
                    FROM memory_entries AS entry
                    JOIN memory_heads AS head ON head.workspace_id=entry.workspace_id
                        AND head.document_id=entry.document_id
                        AND head.revision_id=entry.memory_revision_id
                    JOIN memory_documents AS document USING(workspace_id,document_id)
                    JOIN access_scopes AS scope ON scope.scope_key=entry.scope_key
                    WHERE entry.workspace_id=? AND entry.entry_id=?
                    """,
                    (actor.workspace_id, target_id),
                ).fetchone()
            )
        if row is None:
            return None
        entry = MemoryEntry.model_validate_json(_STRING.validate_python(row[0]))
        document = MemoryDocument.model_validate_json(_STRING.validate_python(row[1]))
        scope = AccessScope.model_validate_json(_STRING.validate_python(row[3]))
        _ = authorize_read(actor=actor, target_scope=scope, at=datetime.now(UTC))
        if entry.wiki_ref is not None:
            expected = self.repository.page_head(actor, entry.wiki_ref.page_id)
            if expected is None:
                _fail("wiki_owner_missing", entry.wiki_ref.page_id)
            return CorrectionTarget(
                target_kind=ProposalTargetKind.WIKI,
                target_id=entry.wiki_ref.page_id,
                expected_revision_id=expected,
                brand_id=None,
                scope=scope,
            )
        return CorrectionTarget(
            target_kind=(
                ProposalTargetKind.SOUL
                if document.kind is MemoryKind.SOUL
                else ProposalTargetKind.MEMORY
            ),
            target_id=document.document_id,
            expected_revision_id=_STRING.validate_python(row[2]),
            brand_id=document.brand_id,
            scope=scope,
        )

    def _memory_document_correction_target(
        self,
        actor: ActorContext,
        document_id: str,
    ) -> CorrectionTarget | None:
        stored = self.repository.read_memory(actor, document_id)
        if stored is None:
            return None
        with self.repository.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT scope.scope_json FROM memory_documents AS document
                    JOIN access_scopes AS scope ON scope.scope_key=document.scope_key
                    WHERE document.workspace_id=? AND document.document_id=?
                    """,
                    (actor.workspace_id, document_id),
                ).fetchone()
            )
        if row is None:
            return None
        scope = AccessScope.model_validate_json(_STRING.validate_python(row[0]))
        return CorrectionTarget(
            target_kind=(
                ProposalTargetKind.SOUL
                if stored.document.kind is MemoryKind.SOUL
                else ProposalTargetKind.MEMORY
            ),
            target_id=document_id,
            expected_revision_id=stored.revision.revision_id,
            brand_id=stored.document.brand_id,
            scope=scope,
        )

    def _page_correction_target(
        self,
        actor: ActorContext,
        target_id: str,
    ) -> CorrectionTarget | None:
        stored = self.repository.read_page(actor, target_id)
        if stored is None:
            with self.repository.connection() as connection:
                row = _OPTIONAL_STRING_ROW.validate_python(
                    connection.execute(
                        """
                        SELECT page_id FROM claim_locations
                        WHERE workspace_id=? AND claim_id=? AND is_current=1
                        """,
                        (actor.workspace_id, target_id),
                    ).fetchone()
                )
            if row is None:
                return None
            stored = self.repository.read_page(actor, _STRING.validate_python(row[0]))
        if stored is None:
            return None
        return CorrectionTarget(
            target_kind=ProposalTargetKind.WIKI,
            target_id=stored.page.page_id,
            expected_revision_id=stored.revision.revision_id,
            brand_id=None,
            scope=stored.page.scope,
        )

    @staticmethod
    def _require_task_actor(actor: ActorContext, binding: TaskBinding) -> None:
        if (
            binding.workspace_id != actor.workspace_id
            or binding.actor_ref != actor.actor_id
            or binding.member_id != actor.member_id
            or binding.session_id != actor.session_id
            or binding.capability_epoch != actor.policy_epoch
            or binding.state.value != "active"
        ):
            _fail("task_scope_mismatch", binding.task_id)

    @staticmethod
    def _may_disclose_scope(actor: ActorContext, scope: AccessScope) -> bool:
        if scope.kind is ScopeKind.WORKSPACE:
            return True
        return actor.conversation_scope.kind is ScopeKind.MEMBER and (
            scope.member_id == actor.member_id and scope.session_id == actor.session_id
        )

    @staticmethod
    def _selection_reason(
        receipt_row: tuple[object, ...] | None,
        target_id: str,
        receipt_id: str | None,
    ) -> str | None:
        if receipt_row is None or receipt_id is None:
            return None
        receipt = ContextReceipt.model_validate_json(_STRING.validate_python(receipt_row[0]))
        selected = any(
            target_id == item.document_id or target_id in item.entry_ids
            for item in receipt.selected_memory_revisions
        ) or any(
            target_id == item.page_id or target_id in item.claim_ids
            for item in receipt.selected_wiki_claims
        )
        if selected:
            return f"Selected by stored context receipt {receipt_id}."
        excluded = next(
            (item.reason.value for item in receipt.exclusions if item.reference_id == target_id),
            None,
        )
        return None if excluded is None else f"Excluded by stored context receipt: {excluded}."

    @staticmethod
    def _adoption_receipt_in(
        connection: sqlite3.Connection,
        actor: ActorContext,
        question: QuestionRecord,
    ) -> ExplicitAdoptionReceipt | None:
        proposal = question.pending_proposal
        if proposal is None:
            return None
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                """
                SELECT receipt_json FROM explicit_adoption_receipts
                WHERE workspace_id=? AND proposal_id=?
                """,
                (actor.workspace_id, proposal.proposal_id),
            ).fetchone()
        )
        return (
            None
            if row is None
            else ExplicitAdoptionReceipt.model_validate_json(_STRING.validate_python(row[0]))
        )


def _fail(code: str, target: str) -> Never:
    raise ToolStateError(code, target)


__all__ = ["CorrectionTarget", "RepositoryToolState", "ToolStateError"]
