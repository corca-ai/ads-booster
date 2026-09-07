from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from multiprocessing import get_context
from multiprocessing.context import ForkContext
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from threading import Event
from typing import TYPE_CHECKING, Protocol, cast

from ads_booster.knowledge.operation_enums import JobPriority, JobState
from ads_booster.knowledge.repository_types import JobClaim, JobCompletion, JobLease

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository


class KnowledgeJobProcessor(Protocol):
    def process(self, lease: JobLease, cancellation: Event) -> JobProcessResult: ...


@dataclass(frozen=True, slots=True)
class JobProcessResult:
    state: JobState
    payload: bytes


@dataclass(slots=True)
class BoundedJobRunner:
    repository: SqliteKnowledgeRepository
    processor: KnowledgeJobProcessor
    worker_id: str
    lease_duration: timedelta = timedelta(seconds=30)
    heartbeat_interval: timedelta = timedelta(seconds=5)
    _context: ForkContext = field(init=False, repr=False)
    _process: BaseProcess | None = field(default=None, init=False, repr=False)
    _queue: Queue[JobProcessResult] = field(init=False, repr=False)
    _lease: JobLease | None = field(default=None, init=False, repr=False)
    _cancel: Event = field(default_factory=Event, init=False, repr=False)
    _last_heartbeat: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._context = cast("ForkContext", get_context("fork"))
        self._queue = self._context.Queue(maxsize=1)

    @property
    def active(self) -> bool:
        return self._process is not None

    def tick(self, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(UTC)
        if self._process is None:
            lease = self.repository.claim_job(
                JobClaim(
                    worker_id=self.worker_id,
                    now=instant,
                    lease_until=instant + self.lease_duration,
                )
            )
            if lease is None:
                return False
            self._cancel = self._context.Event()
            self._lease = lease
            self._last_heartbeat = instant
            self._process = self._context.Process(
                target=_process_job,
                args=(self.processor, lease, self._cancel, self._queue),
                name="knowledge-job",
            )
            self._process.start()
            return True
        lease, process = self._lease, self._process
        if lease is None:
            raise RuntimeError("knowledge_runner_lease_missing")
        if not process.is_alive():
            process.join()
            self._finish(lease, instant)
            return True
        if lease.job.priority is JobPriority.ROUTINE and _urgent_ready(self.repository, instant):
            self._cancel.set()
        if self._last_heartbeat is None or instant - self._last_heartbeat >= self.heartbeat_interval:
            if not _heartbeat(self.repository, lease, instant + self.lease_duration):
                self._cancel.set()
            self._last_heartbeat = instant
        return False

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
            lease = self._lease
            if lease is not None:
                self.repository.finish_job(
                    JobCompletion(
                        job_id=lease.job.job_id,
                        worker_id=lease.worker_id,
                        lease_generation=lease.lease_generation,
                        state=JobState.CANCELLED,
                        result_sha256=sha256(b"knowledge_job_shutdown_cancelled").hexdigest(),
                        completed_at=datetime.now(UTC),
                    )
                )
        self._process = None
        self._lease = None

    def _finish(
        self,
        lease: JobLease,
        now: datetime,
    ) -> None:
        try:
            result = self._queue.get_nowait()
            state = JobState.CANCELLED if self._cancel.is_set() else result.state
            digest = sha256(result.payload).hexdigest()
        except Exception:
            state = JobState.CANCELLED if self._cancel.is_set() else JobState.FAILED
            digest = sha256(state.value.encode()).hexdigest()
        try:
            if state in {JobState.WAITING_DEPENDENCY, JobState.AWAITING_ANSWER}:
                _park(self.repository, lease, state, digest)
            else:
                self.repository.finish_job(
                    JobCompletion(
                        job_id=lease.job.job_id,
                        worker_id=lease.worker_id,
                        lease_generation=lease.lease_generation,
                        state=state,
                        result_sha256=digest,
                        completed_at=now,
                    )
                )
        finally:
            self._process = None
            self._lease = None
            self._last_heartbeat = None


def _process_job(
    processor: KnowledgeJobProcessor,
    lease: JobLease,
    cancellation: Event,
    queue: Queue[JobProcessResult],
) -> None:
    try:
        queue.put(processor.process(lease, cancellation))
    except BaseException:
        queue.put(JobProcessResult(JobState.FAILED, b"knowledge_job_processor_failed"))


def _heartbeat(repository: SqliteKnowledgeRepository, lease: JobLease, lease_until: datetime) -> bool:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE jobs SET lease_expires_at=? WHERE job_id=? AND state='running'
            AND lease_owner=? AND lease_generation=?""",
            (
                lease_until.isoformat(),
                lease.job.job_id,
                lease.worker_id,
                lease.lease_generation,
            ),
        )
        return cursor.rowcount == 1


def _urgent_ready(repository: SqliteKnowledgeRepository, now: datetime) -> bool:
    with repository.connection() as connection:
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT 1 FROM jobs WHERE state='queued' AND priority='urgent' AND due_at<=? LIMIT 1",
                (now.isoformat(),),
            ).fetchone(),
        )
    return row is not None


def _park(
    repository: SqliteKnowledgeRepository,
    lease: JobLease,
    state: JobState,
    result_sha256: str,
) -> None:
    parked = lease.job.model_copy(update={"state": state})
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE jobs SET state=?,lease_owner=NULL,lease_expires_at=NULL,
            result_sha256=?,job_json=? WHERE job_id=? AND state='running'
            AND lease_owner=? AND lease_generation=?""",
            (
                state.value,
                result_sha256,
                parked.model_dump_json(),
                lease.job.job_id,
                lease.worker_id,
                lease.lease_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("job_lease_stale")


__all__ = ["BoundedJobRunner", "JobProcessResult", "KnowledgeJobProcessor"]
