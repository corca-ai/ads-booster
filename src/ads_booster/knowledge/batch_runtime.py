from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from multiprocessing import get_context
from multiprocessing.context import ForkContext
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty
from threading import Event
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import (
    BatchCurationCoordinator,
    ClaimedBatchRun,
    CurationBatchItem,
    CurationBatchWork,
)
from ads_booster.knowledge.contracts import CurationBatch, EventReceipt, KnowledgeJob
from ads_booster.knowledge.curation_contracts import CurationRunStatus
from ads_booster.knowledge.maintenance_jobs import (
    CancellationEvent,
    CanonicalJobProcessor,
    ProcessCancellation,
)
from ads_booster.knowledge.operation_enums import (
    BatchState,
    JobPriority,
    OperationStatus,
)
from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_JOB_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


@dataclass(frozen=True, slots=True)
class CanonicalBatchProcessor:
    jobs: CanonicalJobProcessor

    def process(self, run: ClaimedBatchRun) -> tuple[EventReceipt, ...]:
        receipts: list[EventReceipt] = []
        for result in self.jobs.run_curation_batch(run):
            if result.status in {
                CurationRunStatus.CANCELLED,
                CurationRunStatus.BUDGET_EXHAUSTED,
                CurationRunStatus.PROVIDER_UNAVAILABLE,
            }:
                continue
            receipt = result.event_receipt
            if result.status is CurationRunStatus.FAILED:
                receipt = receipt.model_copy(update={"status": OperationStatus.FAILED})
            receipts.append(receipt)
        return tuple(receipts)


@dataclass(slots=True)
class CurationBatchRuntime:
    repository: SqliteKnowledgeRepository
    actor: ActorContext
    jobs: CanonicalJobProcessor
    _context: ForkContext = field(init=False, repr=False)
    _process: BaseProcess | None = field(default=None, init=False, repr=False)
    _queue: Queue[bool] = field(init=False, repr=False)
    _cancel: CancellationEvent = field(default_factory=Event, init=False, repr=False)
    _batch: CurationBatch | None = field(default=None, init=False, repr=False)
    _batch_actor: ActorContext | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._context = cast("ForkContext", get_context("fork"))
        self._queue = self._context.Queue(maxsize=1)

    @property
    def active(self) -> bool:
        return self._process is not None

    def tick(self, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(UTC)
        collected = self._collect_pending(instant)
        if self._process is not None:
            if not self._process.is_alive():
                self._finish_active()
                return True
            if (
                self._batch is not None
                and self._batch.priority is JobPriority.ROUTINE
                and self._urgent_ready()
            ):
                self._cancel.set()
            return collected
        batch_actor = self._ready_actor(instant)
        if batch_actor is None:
            return collected
        coordinator = BatchCurationCoordinator(self.repository)
        batch = coordinator.claim(batch_actor, instant)
        if batch is None:
            return collected
        try:
            work = self._work_for(batch, batch_actor)
        except Exception:
            _ = coordinator.execute_claimed(
                ClaimedBatchRun(batch_actor, batch, ()),
                CanonicalBatchProcessor(self.jobs),
            )
            return True
        self._cancel = self._context.Event()
        self._batch = batch
        self._batch_actor = batch_actor
        run = ClaimedBatchRun(batch_actor, batch, work, ProcessCancellation(self._cancel))
        self._process = self._context.Process(
            target=_process_batch,
            args=(coordinator, run, CanonicalBatchProcessor(self.jobs), self._queue),
            name="knowledge-curation-batch",
        )
        self._process.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def shutdown(self, *, wait: bool = False) -> None:
        self.cancel()
        process = self._process
        if process is None:
            return
        if wait:
            process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        self._finish_active()

    def _collect_pending(self, now: datetime) -> bool:
        with self.repository.connection() as connection:
            rows = _JOB_ROWS.validate_python(
                connection.execute(
                    """
                    SELECT job_json FROM jobs
                    WHERE workspace_id=? AND kind='curation' AND state='queued'
                        AND batch_id IS NULL AND due_at<=?
                    ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,due_at,job_id
                    """,
                    (self.actor.workspace_id, now.isoformat()),
                ).fetchall()
            )
        coordinator = BatchCurationCoordinator(self.repository)
        for (encoded,) in rows:
            job = KnowledgeJob.model_validate_json(encoded)
            actor = self._actor_for(job)
            work = self.jobs.build_curation_work(job, actor)
            _ = coordinator.collect(
                CurationBatchItem(
                    job_id=job.job_id,
                    event_id=job.root_event_id,
                    event_revision=work.request.event_revision,
                    actor=actor,
                    policy_version=job.policy_version,
                    priority=job.priority,
                    occurred_at=job.created_at,
                )
            )
        return bool(rows)

    def _work_for(
        self,
        batch: CurationBatch,
        actor: ActorContext,
    ) -> tuple[CurationBatchWork, ...]:
        with self.repository.connection() as connection:
            rows = _JOB_ROWS.validate_python(
                connection.execute(
                    "SELECT job_json FROM jobs WHERE batch_id=?",
                    (batch.batch_id,),
                ).fetchall()
            )
        jobs = {
            job.root_event_id: job
            for encoded, in rows
            for job in (KnowledgeJob.model_validate_json(encoded),)
        }
        return tuple(
            self.jobs.build_curation_work(jobs[receipt.event_id], actor)
            for receipt in batch.event_receipts
        )

    def _ready_actor(self, now: datetime) -> ActorContext | None:
        with self.repository.connection() as connection:
            row = connection.execute(
                """SELECT batch_json FROM curation_batches
                WHERE workspace_id=? AND (
                    state='ready' OR (state='collecting' AND batch_deadline<=?)
                ) ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                    batch_deadline,batch_id LIMIT 1""",
                (self.actor.workspace_id, now.isoformat()),
            ).fetchone()
        if row is None:
            return None
        batch = CurationBatch.model_validate_json(row[0])
        return self._actor_for_scope(batch.scope)

    def _actor_for(self, job: KnowledgeJob) -> ActorContext:
        return self._actor_for_scope(job.scope)

    def _actor_for_scope(self, scope: AccessScope) -> ActorContext:
        if scope == self.actor.conversation_scope:
            return self.actor
        if scope.member_id is None or scope.session_id is None:
            raise ValueError("knowledge_batch_scope_identity_missing")
        return self.actor.model_copy(
            update={
                "member_id": scope.member_id,
                "session_id": scope.session_id,
                "conversation_scope": scope,
            }
        )

    def _urgent_ready(self) -> bool:
        with self.repository.connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM curation_batches
                WHERE workspace_id=? AND priority='urgent'
                    AND state IN ('collecting','ready') LIMIT 1""",
                (self.actor.workspace_id,),
            ).fetchone()
        return row is not None

    def _finish_active(self) -> None:
        process, batch, actor = self._process, self._batch, self._batch_actor
        if process is None or batch is None or actor is None:
            return
        process.join()
        try:
            completed = self._queue.get_nowait()
        except Empty:
            completed = False
        if not completed:
            current = self.repository.curation_batch(actor, batch.batch_id)
            if current is not None and current.state is BatchState.RUNNING:
                _ = self.repository.finish_curation_batch(
                    actor,
                    batch.batch_id,
                    (),
                    BatchState.CANCELLED,
                )
        self._process = None
        self._batch = None
        self._batch_actor = None


def _process_batch(
    coordinator: BatchCurationCoordinator,
    run: ClaimedBatchRun,
    processor: CanonicalBatchProcessor,
    queue: Queue[bool],
) -> None:
    try:
        _ = coordinator.execute_claimed(run, processor)
    except BaseException:
        queue.put(False)
    else:
        queue.put(True)


__all__ = ["CanonicalBatchProcessor", "CurationBatchRuntime"]
