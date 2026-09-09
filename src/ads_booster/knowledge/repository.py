from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import OperationReceipt
from ads_booster.knowledge.file_store import ImmutableFileStore
from ads_booster.knowledge.migrations import connect_database, initialize_database
from ads_booster.knowledge.repository_batch import (
    collect_curation_item,
    collecting_curation_batch,
    curation_batch,
    finish_curation_batch,
    ready_curation_batch,
)
from ads_booster.knowledge.repository_commit import commit_catalog
from ads_booster.knowledge.repository_deletion import memory_history_redaction
from ads_booster.knowledge.repository_dependency import claim_dependency_state
from ads_booster.knowledge.repository_identity import register_actor as register_actor_rows
from ads_booster.knowledge.repository_jobs import claim_job, finish_job, put_job
from ads_booster.knowledge.repository_memory import (
    brand,
    read_memory,
    register_brand,
)
from ads_booster.knowledge.repository_page import page_head, read_page, resolve_page_id
from ads_booster.knowledge.repository_resolution import (
    ResolvedEvidence,
    memory_dependents,
    resolve_evidence,
)
from ads_booster.knowledge.repository_run_binding import (
    put_run_binding,
    run_binding,
)
from ads_booster.knowledge.repository_skills import (
    mark_skill_display_current,
    read_skill,
    skill_ids,
)
from ads_booster.knowledge.repository_source import (
    change_source_admission,
    latest_source_observation,
    read_source,
    record_source_observation,
    register_source,
    source_by_identity,
    source_ingest_head,
)
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.repository_transfer import (
    context_transfer,
    record_context_transfer,
    record_transfer_replica,
    record_transfer_validation,
    transfer_dependencies,
    transfer_validation,
)
from ads_booster.knowledge.repository_types import (
    BrandRegistration,
    CatalogCommit,
    CrashInjector,
    EvidenceDependencyInvalidation,
    HeadExpectation,
    IndexOutboxItem,
    JobClaim,
    JobCompletion,
    JobLease,
    JobRegistration,
    MembershipRole,
    MemoryRevisionWrite,
    PageRedirect,
    PageRevisionWrite,
    RepositoryCommitBoundary,
    RepositoryConflictError,
    RunBinding,
    RunBindingState,
    SourceAdmissionChange,
    SourceObservationWrite,
    SourceRegistration,
    StoredMemory,
    StoredPage,
    StoredSkill,
    StoredSource,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from datetime import date, datetime
    from pathlib import Path

    from ads_booster.contracts.knowledge_context import (
        ContextTransferValidationAccepted,
        ContextTransferValidationRejected,
        ContextTransferValidationRequest,
        KnowledgeContextTransfer,
    )
    from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
    from ads_booster.knowledge.contract_types import MemoryKind
    from ads_booster.knowledge.contracts import (
        ActorContext,
        Brand,
        CurationBatch,
        DependencyState,
        EventReceipt,
        EvidenceRef,
        IngestReceipt,
        Source,
        SourceKind,
    )
    from ads_booster.knowledge.governance_contracts import TaskBinding, TaskOverlay
    from ads_booster.knowledge.operation_contracts import MemoryExplanation
    from ads_booster.knowledge.operation_enums import BatchState, JobPriority
    from ads_booster.knowledge.repository_tool_state import CorrectionTarget
    from ads_booster.knowledge.source_contracts import ConversationEvent
    from ads_booster.knowledge.tool_contracts import QuestionRecord, ScheduledKnowledgeRequest


_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


def _ignore_commit_boundary(boundary: RepositoryCommitBoundary) -> None:
    _ = boundary


@dataclass(frozen=True, slots=True)
class SqliteKnowledgeRepository:
    root: Path
    crash_injector: CrashInjector = field(
        default=_ignore_commit_boundary,
        repr=False,
        compare=False,
    )
    files: ImmutableFileStore = field(init=False)
    database_path: Path = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the private file store and normalized catalog."""
        files = ImmutableFileStore(self.root)
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "database_path", initialize_database(self.root))

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        with connect_database(self.database_path) as connection:
            yield connection

    def reach_commit_boundary(self, boundary: RepositoryCommitBoundary) -> None:
        self.crash_injector(boundary)

    def register_actor(self, actor: ActorContext, role: MembershipRole) -> None:
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            register_actor_rows(connection, actor, role)

    def register_source(self, command: SourceRegistration) -> IngestReceipt:
        return register_source(self, command)

    def change_source_admission(self, command: SourceAdmissionChange) -> Source:
        return change_source_admission(self, command)

    def read_source(self, actor: ActorContext, source_id: str) -> StoredSource | None:
        return read_source(self, actor, source_id)

    def source_by_identity(
        self,
        actor: ActorContext,
        source_kind: SourceKind,
        source_identity: str,
    ) -> Source | None:
        return source_by_identity(self, actor, source_kind, source_identity)

    def source_ingest_head(
        self,
        actor: ActorContext,
        source_kind: SourceKind,
        source_identity: str,
    ) -> Source | None:
        return source_ingest_head(self, actor, source_kind, source_identity)

    def latest_source_observation(
        self,
        actor: ActorContext,
        source_id: str,
    ) -> SourceObservationWrite | None:
        return latest_source_observation(self, actor, source_id)

    def record_source_observation(
        self,
        actor: ActorContext,
        observation: SourceObservationWrite,
    ) -> bool:
        return record_source_observation(self, actor, observation)

    def pending_index_items(self, workspace_id: str) -> tuple[str, ...]:
        with self.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """
                    SELECT item_id FROM index_outbox
                    WHERE workspace_id=? AND state='pending' ORDER BY rowid
                    """,
                    (workspace_id,),
                ).fetchall()
            )
        return tuple(row[0] for row in rows)

    def commit_catalog(self, command: CatalogCommit) -> OperationReceipt:
        return commit_catalog(self, command)

    def put_run_binding(self, actor: ActorContext, binding: RunBinding) -> RunBinding:
        return put_run_binding(self, actor, binding)

    def run_binding(self, actor: ActorContext, binding_id: str) -> RunBinding | None:
        return run_binding(self, actor, binding_id)

    def register_brand(self, command: BrandRegistration) -> OperationReceipt:
        return register_brand(self, command)

    def brand(self, actor: ActorContext, brand_id: str) -> Brand | None:
        return brand(self, actor, brand_id)

    def read_page(
        self,
        actor: ActorContext,
        page_id: str,
        revision_id: str | None = None,
    ) -> StoredPage | None:
        return read_page(self, actor, page_id, revision_id)

    def page_head(self, actor: ActorContext, page_id: str) -> str | None:
        return page_head(self, actor, page_id)

    def resolve_page_id(self, actor: ActorContext, page_id: str) -> str:
        return resolve_page_id(self, actor, page_id)

    def read_memory(
        self,
        actor: ActorContext,
        document_id: str,
        revision_id: str | None = None,
    ) -> StoredMemory | None:
        return read_memory(self, actor, document_id, revision_id)

    def read_skill(
        self,
        actor: ActorContext,
        skill_id: str,
        revision_id: str | None = None,
    ) -> StoredSkill | None:
        return read_skill(self, actor, skill_id, revision_id)

    def skill_ids(self, actor: ActorContext) -> tuple[str, ...]:
        return skill_ids(self, actor)

    def skill_head(self, actor: ActorContext, skill_id: str) -> str | None:
        stored = read_skill(self, actor, skill_id)
        return None if stored is None else stored.record.version

    def mark_skill_display_current(
        self,
        workspace_id: str,
        skill_id: str,
        revision_id: str,
    ) -> None:
        mark_skill_display_current(self, workspace_id, skill_id, revision_id)

    def memory_history_redaction(
        self,
        workspace_id: str,
        document_id: str,
        revision_id: str,
    ) -> tuple[str, str | None] | None:
        return memory_history_redaction(self, workspace_id, document_id, revision_id)

    def open_task(self, actor: ActorContext, binding: TaskBinding) -> TaskBinding:
        return RepositoryToolState(self).open_task(actor, binding)

    def close_task(
        self,
        actor: ActorContext,
        task_id: str,
        capability_epoch: int,
        closed_at: datetime,
    ) -> TaskBinding:
        return RepositoryToolState(self).close_task(actor, task_id, capability_epoch, closed_at)

    def task_binding(self, actor: ActorContext, task_id: str) -> TaskBinding | None:
        return RepositoryToolState(self).task_binding(actor, task_id)

    def put_task_overlay(self, actor: ActorContext, overlay: TaskOverlay) -> TaskOverlay:
        return RepositoryToolState(self).put_task_overlay(actor, overlay)

    def active_task_overlays(
        self,
        actor: ActorContext,
        task_id: str,
    ) -> tuple[TaskOverlay, ...]:
        return RepositoryToolState(self).active_task_overlays(actor, task_id)

    def canonical_event(self, actor: ActorContext, event_ref: str) -> ConversationEvent:
        return RepositoryToolState(self).canonical_event(actor, event_ref)

    def mark_event_source_use_only(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        operation_id: str,
        occurred_at: datetime,
    ) -> bool:
        return RepositoryToolState(self).mark_event_source_use_only(
            actor,
            event,
            operation_id,
            occurred_at,
        )

    def find_memory_document_id(
        self,
        actor: ActorContext,
        kind: MemoryKind,
        brand_id: str | None,
        local_date: date | None,
    ) -> str | None:
        return RepositoryToolState(self).find_memory_document_id(
            actor,
            kind,
            brand_id,
            local_date,
        )

    def correction_target(self, actor: ActorContext, target_id: str) -> CorrectionTarget:
        return RepositoryToolState(self).correction_target(actor, target_id)

    def explain(
        self,
        actor: ActorContext,
        target_id: str,
        task_receipt_id: str | None,
    ) -> MemoryExplanation | None:
        return RepositoryToolState(self).explain(actor, target_id, task_receipt_id)

    def put_question(
        self,
        actor: ActorContext,
        question: QuestionRecord,
    ) -> tuple[QuestionRecord, bool]:
        return RepositoryToolState(self).put_question(actor, question)

    def question(self, actor: ActorContext, question_id: str) -> QuestionRecord | None:
        return RepositoryToolState(self).question(actor, question_id)

    def answer_question(
        self,
        actor: ActorContext,
        answered: QuestionRecord,
        receipt: ExplicitAdoptionReceipt | None,
    ) -> tuple[QuestionRecord, ExplicitAdoptionReceipt | None, bool]:
        return RepositoryToolState(self).answer_question(actor, answered, receipt)

    def adoption_receipts(
        self,
        actor: ActorContext,
        receipt_ids: tuple[str, ...],
    ) -> tuple[ExplicitAdoptionReceipt, ...]:
        return RepositoryToolState(self).adoption_receipts(actor, receipt_ids)

    def adoption_receipt(
        self,
        actor: ActorContext,
        receipt_id: str,
    ) -> ExplicitAdoptionReceipt | None:
        return RepositoryToolState(self).adoption_receipt(actor, receipt_id)

    def job_exists(self, actor: ActorContext, job_id: str) -> bool:
        return RepositoryToolState(self).job_exists(actor, job_id)

    def read_source_extract(
        self,
        actor: ActorContext,
        source_id: str,
        revision_id: str | None,
    ) -> StoredSource | None:
        return RepositoryToolState(self).read_source_extract(actor, source_id, revision_id)

    def put_scheduled_job(
        self,
        actor: ActorContext,
        registration: JobRegistration,
        request: ScheduledKnowledgeRequest,
    ) -> bool:
        return RepositoryToolState(self).put_scheduled_job(actor, registration, request)

    def scheduled_job_request(
        self,
        actor: ActorContext,
        job_id: str,
    ) -> ScheduledKnowledgeRequest | None:
        return RepositoryToolState(self).scheduled_job_request(actor, job_id)

    def pending_memory_views(self, workspace_id: str) -> tuple[str, ...]:
        with self.connection() as connection:
            rows = _STRING_ROWS.validate_python(
                connection.execute(
                    """
                    SELECT revision_id FROM memory_view_outbox
                    WHERE workspace_id=? AND state='pending' ORDER BY rowid
                    """,
                    (workspace_id,),
                ).fetchall()
            )
        return tuple(row[0] for row in rows)

    def operation_receipt(
        self,
        actor: ActorContext,
        operation_id: str,
    ) -> OperationReceipt | None:
        with self.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """
                    SELECT operation.receipt_json FROM operations AS operation
                    WHERE operation.workspace_id=? AND operation.operation_id=?
                    """,
                    (actor.workspace_id, operation_id),
                ).fetchone()
            )
        return None if row is None else OperationReceipt.model_validate_json(row[0])

    def put_job(self, registration: JobRegistration) -> None:
        put_job(self, registration)

    def collect_curation_item(
        self,
        actor: ActorContext,
        batch: CurationBatch,
        job_id: str,
        receipt: EventReceipt,
    ) -> CurationBatch:
        return collect_curation_item(self, actor, batch, job_id, receipt)

    def curation_batch(self, actor: ActorContext, batch_id: str) -> CurationBatch | None:
        return curation_batch(self, actor, batch_id)

    def collecting_curation_batch(
        self,
        actor: ActorContext,
        priority: JobPriority,
        policy_version: str,
        read_grant_sha256: str,
        write_capability_sha256: str,
    ) -> CurationBatch | None:
        return collecting_curation_batch(
            self,
            actor,
            priority,
            policy_version,
            read_grant_sha256,
            write_capability_sha256,
        )

    def ready_curation_batch(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> CurationBatch | None:
        return ready_curation_batch(self, actor, now)

    def finish_curation_batch(
        self,
        actor: ActorContext,
        batch_id: str,
        event_receipts: tuple[EventReceipt, ...],
        state: BatchState,
    ) -> CurationBatch:
        return finish_curation_batch(self, actor, batch_id, event_receipts, state)

    def claim_job(self, claim: JobClaim) -> JobLease | None:
        return claim_job(self, claim)

    def finish_job(self, completion: JobCompletion) -> None:
        finish_job(self, completion)

    def record_context_transfer(self, transfer: KnowledgeContextTransfer) -> bool:
        return record_context_transfer(self, transfer)

    def context_transfer(self, transfer_id: str) -> KnowledgeContextTransfer | None:
        return context_transfer(self, transfer_id)

    def transfer_dependencies(self, transfer_id: str) -> tuple[tuple[str, str, str], ...]:
        return transfer_dependencies(self, transfer_id)

    def transfer_validation(
        self,
        transfer_id: str,
        stage: str,
        request_id: str,
    ) -> ContextTransferValidationAccepted | ContextTransferValidationRejected | None:
        return transfer_validation(self, transfer_id, stage, request_id)

    def record_transfer_validation(
        self,
        request: ContextTransferValidationRequest,
        result: ContextTransferValidationAccepted | ContextTransferValidationRejected,
    ) -> None:
        record_transfer_validation(self, request, result)

    def record_transfer_replica(
        self,
        transfer_id: str,
        system_id: str,
        replica_id: str,
    ) -> None:
        record_transfer_replica(self, transfer_id, system_id, replica_id)

    def resolve_evidence(
        self,
        actor: ActorContext,
        reference: EvidenceRef,
    ) -> ResolvedEvidence:
        return resolve_evidence(self, actor, reference)

    def claim_dependency_state(
        self,
        actor: ActorContext,
        claim_id: str,
        revision_id: str,
    ) -> DependencyState | None:
        return claim_dependency_state(self, actor, claim_id, revision_id)

    def memory_dependents(
        self,
        actor: ActorContext,
        evidence_ids: tuple[str, ...],
    ) -> tuple[StoredMemory, ...]:
        return memory_dependents(self, actor, evidence_ids)


__all__ = [
    "BrandRegistration",
    "CatalogCommit",
    "CrashInjector",
    "EvidenceDependencyInvalidation",
    "HeadExpectation",
    "IndexOutboxItem",
    "JobClaim",
    "JobCompletion",
    "JobLease",
    "JobRegistration",
    "MembershipRole",
    "MemoryRevisionWrite",
    "PageRedirect",
    "PageRevisionWrite",
    "RepositoryCommitBoundary",
    "RepositoryConflictError",
    "RunBinding",
    "RunBindingState",
    "SourceAdmissionChange",
    "SourceObservationWrite",
    "SourceRegistration",
    "SqliteKnowledgeRepository",
]
