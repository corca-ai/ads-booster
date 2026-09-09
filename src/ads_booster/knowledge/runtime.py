from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.maintenance import KnowledgeActivity, KnowledgeOwner
from ads_booster.knowledge.repository_batch import flush_curation_batches

if TYPE_CHECKING:
    from ads_booster.knowledge.jobs import BoundedJobRunner
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository


class WorkDispatcher(Protocol):
    def dispatch_once(self) -> bool: ...


class IndexDispatcher(Protocol):
    def run_once(self, workspace_id: str, worker_id: str, now: datetime) -> object: ...


class BatchFlusher(Protocol):
    def flush_ready_batches(self, workspace_id: str, now: datetime) -> int: ...


class CurationBatchDispatcher(Protocol):
    @property
    def active(self) -> bool: ...

    def tick(self, *, now: datetime | None = None) -> bool: ...

    def cancel(self) -> None: ...

    def shutdown(self, *, wait: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class SqliteBatchFlusher:
    repository: SqliteKnowledgeRepository

    def flush_ready_batches(self, workspace_id: str, now: datetime) -> int:
        return flush_curation_batches(self.repository, workspace_id, now)


@dataclass(slots=True)
class KnowledgeRuntime:
    workspace_id: str
    owner: KnowledgeOwner
    jobs: BoundedJobRunner
    ingress: WorkDispatcher | None = None
    experiences: WorkDispatcher | None = None
    index: IndexDispatcher | None = None
    memory_views: WorkDispatcher | None = None
    batches: BatchFlusher | None = None
    curation_batches: CurationBatchDispatcher | None = None
    poll_seconds: float = 1.0
    activity: KnowledgeActivity = field(default_factory=KnowledgeActivity)
    stop: Event = field(default_factory=Event)
    _job_accounted: bool = field(default=False, init=False, repr=False)
    _batch_accounted: bool = field(default=False, init=False, repr=False)

    @property
    def active(self) -> bool:
        return self.jobs.active or (
            self.curation_batches is not None and self.curation_batches.active
        )

    def run_once(self, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(UTC)
        self.owner.heartbeat(now=instant)
        worked = False
        for kind, dispatcher in (
            ("ingress", self.ingress),
            ("experience", self.experiences),
            ("memory_view", self.memory_views),
        ):
            if dispatcher is not None and self.activity.claim(kind):
                try:
                    worked = dispatcher.dispatch_once() or worked
                finally:
                    self.activity.finish(kind)
        batch_runner = self.curation_batches
        if batch_runner is not None:
            was_batch_active = batch_runner.active
            if was_batch_active or self.activity.claim("curation"):
                self._batch_accounted = self._batch_accounted or not was_batch_active
                worked = batch_runner.tick(now=instant) or worked
                if self._batch_accounted and not batch_runner.active:
                    self.activity.finish("curation")
                    self._batch_accounted = False
        was_active = self.jobs.active
        if was_active or self.activity.claim("job"):
            self._job_accounted = self._job_accounted or not was_active
            worked = self.jobs.tick(now=instant) or worked
            if self._job_accounted and not self.jobs.active:
                self.activity.finish("job")
                self._job_accounted = False
        if self.index is not None and self.activity.claim("index"):
            try:
                result = self.index.run_once(self.workspace_id, self.owner.owner_id, instant)
                worked = getattr(result, "state", "idle") != "idle" or worked
            finally:
                self.activity.finish("index")
        return worked

    def run_until_idle(self, *, flush_batches: bool = False) -> None:
        if flush_batches:
            if self.batches is None:
                message = "knowledge_batch_flush_unavailable"
                raise ValueError(message)
            _ = self.run_once()
            _ = self.batches.flush_ready_batches(self.workspace_id, datetime.now(UTC))
        while not self.stop.is_set():
            if not self.run_once() and not self.active:
                return
            _ = self.stop.wait(0.01)

    def run_continuous(self) -> None:
        while not self.stop.is_set():
            worked = self.run_once()
            if not worked:
                _ = self.stop.wait(self.poll_seconds)

    def request_stop(self) -> None:
        self.activity.begin_maintenance()
        self.stop.set()
        if self.curation_batches is not None:
            self.curation_batches.cancel()
        self.jobs.cancel()

    def close(self) -> None:
        self.request_stop()
        if self.curation_batches is not None:
            self.curation_batches.shutdown(wait=True)
        self.jobs.shutdown(wait=True)
        self.owner.release()


__all__ = [
    "BatchFlusher",
    "CurationBatchDispatcher",
    "IndexDispatcher",
    "KnowledgeRuntime",
    "SqliteBatchFlusher",
    "WorkDispatcher",
]
