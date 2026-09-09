from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, cast

from ads_booster.knowledge.batch_curation import ClaimedBatchRun, CurationBatchWork
from ads_booster.knowledge.batch_actor import load_partition_actor
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    ConversationRole,
    EvidenceKind,
    InstructionAuthority,
    Provenance,
    SourceKind,
)
from ads_booster.knowledge.curation_contracts import (
    CurationExcerpt,
    CurationRequest,
    CurationResult,
    CurationRunStatus,
    CurationUserEvent,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.jobs import JobProcessResult
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.source_contracts import ConversationEvent
from ads_booster.knowledge.tool_contracts import (
    TrustedInvocationContext,
    TrustedSourceCapability,
)

if TYPE_CHECKING:
    from threading import Event

    from ads_booster.knowledge.contracts import KnowledgeJob
    from ads_booster.knowledge.curation import CurationRunner
    from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard
    from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_types import JobLease, StoredSource
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_review_jobs import SourceReviewJobProcessor


class CancellationEvent(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessCancellation:
    event: CancellationEvent

    def cancelled(self) -> bool:
        return self.event.is_set()


@dataclass(frozen=True, slots=True)
class CanonicalJobProcessor:
    repository: SqliteKnowledgeRepository
    actor: ActorContext
    curation: CurationRunner
    memory: MemoryConsolidationProcessor
    source_review: SourceReviewJobProcessor | None = None
    legacy_memory: LegacyMemoryGuard | None = None

    def process(self, lease: JobLease, cancellation: Event) -> JobProcessResult:
        if lease.job.kind in {
            JobKind.MEMORY_CONSOLIDATE,
            JobKind.MEMORY_SUMMARY_REFRESH,
            JobKind.MEMORY_VIEW_REFRESH,
        }:
            return self.memory.process(lease, cancellation)
        if lease.job.kind is JobKind.SOURCE_REVIEW:
            processor = self.source_review
            if processor is None:
                return JobProcessResult(JobState.WAITING_DEPENDENCY, b"source_review_unavailable")
            return processor.process(lease, cancellation)
        if lease.job.kind is not JobKind.CURATION:
            return JobProcessResult(JobState.WAITING_DEPENDENCY, b"job_handler_unavailable")
        result = self.run_curation_work(
            self.build_curation_work(lease.job),
            ProcessCancellation(cancellation),
            lease.job.priority,
        )
        return JobProcessResult(_job_state(result.status), result.model_dump_json().encode())

    def build_curation_work(
        self,
        job: KnowledgeJob,
        actor: ActorContext | None = None,
    ) -> CurationBatchWork:
        started_at = datetime.now(UTC)
        learning = LearningReviewCoordinator(self.repository)
        learning_review = learning.review_for_job(job.job_id)
        bound_actor = None if learning_review is None else learning.source_actor_for_job(job.job_id)
        if learning_review is not None and bound_actor is None:
            msg = "learning_job_actor_binding_missing"
            raise ValueError(msg)
        scoped_actor = actor or (
            self.actor
            if bound_actor is None
            else load_partition_actor(self.repository, bound_actor, started_at)
        )
        if bound_actor is not None and (
            scoped_actor.actor_id != bound_actor.actor_id
            or scoped_actor.workspace_id != bound_actor.workspace_id
            or scoped_actor.member_id != bound_actor.member_id
            or scoped_actor.session_id != bound_actor.session_id
        ):
            msg = "learning_job_actor_binding_mismatch"
            raise ValueError(msg)
        source_id, revision_id = self._source_for_job(job.job_id)
        source = self.repository.read_source(scoped_actor, source_id)
        if source is None or source.source.revision_id != revision_id:
            msg = "curation_source_unavailable"
            raise ValueError(msg)
        extracted = RepositoryToolState(self.repository).read_source_extract(
            scoped_actor, source_id, revision_id
        )
        if extracted is None:
            msg = "curation_source_unavailable"
            raise ValueError(msg)
        body = extracted.body.decode("utf-8")
        excerpts = tuple(
            CurationExcerpt(
                source_id=source_id,
                revision_id=revision_id,
                segment_id=segment.segment_id,
                locator=segment.locator.model_dump_json(),
                text=body[segment.quote_range.start : segment.quote_range.end][:20_000],
                completeness=segment.completeness,
            )
            for segment in extracted.segments[:20]
        )
        authenticated_user_event = self._authenticated_user_event(scoped_actor, source)
        source_event = (
            None
            if authenticated_user_event is None
            else self.repository.canonical_event(
                scoped_actor, authenticated_user_event.evidence_ref.evidence_id
            )
        )
        legacy_selection = None
        if learning_review is not None and self.legacy_memory is not None:
            source_run_id = learning.source_run_id_for_job(job.job_id)
            if source_run_id is None:
                msg = "learning_job_run_binding_missing"
                raise ValueError(msg)
            legacy_selection = self.legacy_memory.select(
                scoped_actor,
                run_id=source_run_id,
                query=body,
                now=started_at,
            )
        request = CurationRequest(
            schema="knowledge.curation-request.v1",
            job_id=job.job_id,
            event_id=job.root_event_id,
            event_revision=source.source.revision,
            policy_version=job.policy_version,
            objective=body[:20_000] or "Review the source disposition.",
            excerpts=excerpts,
            authenticated_user_event=authenticated_user_event,
            learning_purpose=(None if learning_review is None else learning_review.purpose),
            learning_review=learning_review,
            legacy_memory_selection=legacy_selection,
            started_at=started_at,
        )
        context = TrustedInvocationContext(
            invocation_id=f"curation.{job.job_id}",
            actor=scoped_actor,
            run_binding_id=f"background.{job.job_id}",
            run_id=(
                f"background.{job.job_id}"
                if legacy_selection is None
                else legacy_selection.receipt.run_id
            ),
            job_id=job.job_id,
            capability_epoch=scoped_actor.policy_epoch,
            source_capabilities=(
                TrustedSourceCapability(
                    capability_id=f"source.{job.job_id}",
                    workspace_id=scoped_actor.workspace_id,
                    actor_ref=scoped_actor.actor_id,
                    source_id=source_id,
                    revision_id=revision_id,
                    segment_ids=tuple(segment.segment_id for segment in extracted.segments),
                    allows_unadmitted_read=True,
                ),
            ),
            source_fetch_event=source_event,
            legacy_memory_selection=legacy_selection,
            invoked_at=started_at,
        )
        return CurationBatchWork(request=request, trusted_context=context)

    def _authenticated_user_event(
        self, actor: ActorContext, source: StoredSource
    ) -> CurationUserEvent | None:
        if source.source.source_kind is not SourceKind.MESSAGE:
            return None
        original = ConversationEvent.model_validate_json(source.body)
        if original.role is not ConversationRole.USER or original.quoted_spans:
            return None
        event = self.repository.canonical_event(actor, original.message_id)
        if event != original:
            msg = "curation_event_binding_mismatch"
            raise ValueError(msg)
        return CurationUserEvent(
            evidence_ref=EvidenceRef(
                evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                evidence_id=event.message_id,
                revision_id=str(event.revision),
                quote_sha256=sha256(event.text.encode()).hexdigest(),
                scope=event.scope,
                instruction_authority=InstructionAuthority.AUTHORIZED_USER,
                provenance=Provenance.HUMAN_DIRECT,
            ),
            authority_ref=AuthorityRef(
                event_id=event.message_id,
                authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
                actor_ref=event.speaker_ref,
                workspace_id=event.scope.workspace_id,
                scope=event.scope,
                policy_epoch=actor.policy_epoch,
            ),
        )

    def run_curation_work(
        self,
        work: CurationBatchWork,
        cancellation: ProcessCancellation,
        priority: JobPriority,
    ) -> CurationResult:
        result = self.curation.run(work.request, work.trusted_context, cancellation)
        if result.applied_operation_ids:
            _ = self.memory.schedule(
                root_event_id=work.request.event_id,
                due_at=datetime.now(UTC),
                priority=priority,
            )
        return result

    def run_curation_batch(self, run: ClaimedBatchRun) -> tuple[CurationResult, ...]:
        results = self.curation.run_batch(
            run.batch.batch_id,
            run.items,
            run.cancellation,
        )
        for result in results:
            if result.applied_operation_ids:
                _ = self.memory.schedule(
                    root_event_id=result.event_receipt.event_id,
                    due_at=datetime.now(UTC),
                    priority=run.batch.priority,
                )
        return results

    def _source_for_job(self, job_id: str) -> tuple[str, str]:
        with self.repository.connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT source_id,source_revision_id FROM delivery_receipts WHERE job_id=?",
                    (job_id,),
                ).fetchone(),
            )
        if row is None:
            msg = "curation_delivery_receipt_missing"
            raise ValueError(msg)
        return str(row[0]), str(row[1])


def _job_state(status: CurationRunStatus) -> JobState:
    if status is CurationRunStatus.FINISHED:
        return JobState.COMPLETED
    if status is CurationRunStatus.AWAITING_ANSWER:
        return JobState.AWAITING_ANSWER
    if status in {CurationRunStatus.BUDGET_EXHAUSTED, CurationRunStatus.PROVIDER_UNAVAILABLE}:
        return JobState.WAITING_DEPENDENCY
    if status is CurationRunStatus.CANCELLED:
        return JobState.CANCELLED
    return JobState.FAILED


__all__ = ["CancellationEvent", "CanonicalJobProcessor", "ProcessCancellation"]
