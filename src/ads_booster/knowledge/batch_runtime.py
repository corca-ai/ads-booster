from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from multiprocessing import get_context
from queue import Empty
from threading import Event
from typing import TYPE_CHECKING, assert_never

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_actor import load_batch_actor, load_job_actor
from ads_booster.knowledge.batch_curation import (
    BatchCurationCoordinator,
    ClaimedBatchRun,
    CurationBatchItem,
    CurationBatchWork,
)
from ads_booster.knowledge.batch_failure import fail_unbatched_job, fail_unclaimed_batch
from ads_booster.knowledge.contracts import CurationBatch, EventReceipt, KnowledgeJob
from ads_booster.knowledge.curation_contracts import CurationRunStatus
from ads_booster.knowledge.errors import KnowledgePolicyError
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

if TYPE_CHECKING:
    from multiprocessing.context import ForkContext
    from multiprocessing.process import BaseProcess
    from multiprocessing.queues import Queue

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext

_READY_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_JOB_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


@dataclass(frozen=True, slots=True)
class CanonicalBatchProcessor:
    jobs: CanonicalJobProcessor

    def process(self, run: ClaimedBatchRun) -> tuple[EventReceipt, ...]:
        receipts: list[EventReceipt] = []
        for result in self.jobs.run_curation_batch(run):
            match result.status:
                case CurationRunStatus.CANCELLED:
                    continue
                case (
                    CurationRunStatus.FAILED
                    | CurationRunStatus.BUDGET_EXHAUSTED
                    | CurationRunStatus.PROVIDER_UNAVAILABLE
                ):
                    receipt = result.event_receipt.model_copy(
                        update={"status": OperationStatus.FAILED}
                    )
                case CurationRunStatus.FINISHED | CurationRunStatus.AWAITING_ANSWER:
                    receipt = result.event_receipt
                case _:
                    assert_never(result.status)
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
        """Allocate the process channel owned by this runtime."""
        self._context = get_context("fork")
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
        claimed = self._claim_ready_batch(instant)
        if claimed is None:
            return collected
        batch, batch_actor = claimed
        coordinator = BatchCurationCoordinator(self.repository)
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
            try:
                actor = load_job_actor(self.repository, job, self.actor, now)
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
            except KnowledgePolicyError as error:
                fail_unbatched_job(self.repository, job, error.code)
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
            for (encoded,) in rows
            for job in (KnowledgeJob.model_validate_json(encoded),)
        }
        return tuple(
            self.jobs.build_curation_work(jobs[receipt.event_id], actor)
            for receipt in batch.event_receipts
        )

    def _claim_ready_batch(self, now: datetime) -> tuple[CurationBatch, ActorContext] | None:
        with self.repository.connection() as connection:
            rows = _JOB_ROWS.validate_python(
                connection.execute(
                    """SELECT batch_json FROM curation_batches
                WHERE workspace_id=? AND (
                    state='ready' OR (state='collecting' AND batch_deadline<=?)
                ) ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                    batch_deadline,batch_id""",
                    (self.actor.workspace_id, now.isoformat()),
                ).fetchall()
            )
        for (encoded,) in rows:
            batch = CurationBatch.model_validate_json(encoded)
            try:
                actor = self._actor_for_scope(batch.scope, now, submitter=batch.submitter_actor)
                claimed = BatchCurationCoordinator(self.repository).claim(
                    actor, now, batch_id=batch.batch_id
                )
            except KnowledgePolicyError as error:
                fail_unclaimed_batch(self.repository, batch, error.code)
                continue
            if claimed is not None:
                return claimed, actor
        return None

    def _actor_for_scope(
        self, scope: AccessScope, now: datetime, *, submitter: ActorContext | None = None
    ) -> ActorContext:
        if submitter is not None:
            return load_batch_actor(self.repository, scope, now, submitter=submitter)
        if scope == self.actor.conversation_scope:
            return self.actor
        return load_batch_actor(self.repository, scope, now)

    def _urgent_ready(self) -> bool:
        with self.repository.connection() as connection:
            row = _READY_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM curation_batches
                WHERE workspace_id=? AND priority='urgent'
                    AND state IN ('collecting','ready') LIMIT 1""",
                    (self.actor.workspace_id,),
                ).fetchone()
            )
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
