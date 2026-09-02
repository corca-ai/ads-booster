from __future__ import annotations

import logging
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.marketing.errors import CloudflareQueueError
from ads_booster.marketing.models import WorkerTaskEventType

if TYPE_CHECKING:
    from collections.abc import Callable

_DEFAULT_MAX_PENDING_EVENTS: Final = 64
_CLOSE_JOIN_TIMEOUT_SECONDS: Final = 0.1
_QUEUE_WAIT_SECONDS: Final = 0.1

logger = logging.getLogger(__name__)


class WorkerEventDelivery(Protocol):
    def report_event(
        self,
        task_id: str,
        event_type: WorkerTaskEventType,
        failure_code: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkerTaskEvent:
    task_id: str
    event_type: WorkerTaskEventType
    failure_code: str | None


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class QueuedWorkerEventReporter:
    """Bounded daemon delivery for non-authoritative workspace diagnostics."""

    delivery: WorkerEventDelivery
    max_pending_events: int = _DEFAULT_MAX_PENDING_EVENTS
    on_stop: Callable[[], None] | None = None
    _closed: Event = field(init=False, repr=False)
    _events: Queue[WorkerTaskEvent] = field(init=False, repr=False)
    _thread: Thread = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Start the one daemon that may block on diagnostic delivery."""
        self._closed = Event()
        self.max_pending_events = max(1, self.max_pending_events)
        self._events = Queue(maxsize=self.max_pending_events)
        self._thread = Thread(
            target=self._deliver,
            name="trace-marketing-worker-events",
            daemon=True,
        )
        self._thread.start()

    def report(
        self,
        task_id: str,
        event_type: WorkerTaskEventType,
        failure_code: str | None = None,
    ) -> None:
        """Queue one diagnostic event without waiting for control-plane I/O."""
        try:
            self._events.put_nowait(WorkerTaskEvent(task_id, event_type, failure_code))
        except Full:
            return

    def close(self) -> None:
        """Stop waiting for queued diagnostics during worker shutdown."""
        self._closed.set()
        self._thread.join(timeout=_CLOSE_JOIN_TIMEOUT_SECONDS)

    def _deliver(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    event = self._events.get(timeout=_QUEUE_WAIT_SECONDS)
                except Empty:
                    continue
                try:
                    self.delivery.report_event(
                        event.task_id,
                        event.event_type,
                        event.failure_code,
                    )
                except CloudflareQueueError:
                    logger.debug("worker event delivery failed for %s", event.event_type)
                finally:
                    self._events.task_done()
        finally:
            if self.on_stop is not None:
                self.on_stop()
