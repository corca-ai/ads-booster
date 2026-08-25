from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from trace_capture.marketing.cloudflare_queue import CloudflareQueueError
from trace_capture.marketing.inbox import (
    InboxConflictError,
    MarketingExecutionError,
    MarketingInbox,
)
from trace_capture.marketing.models import (
    MarketingTask,
    QueueLease,
    TaskCallback,
    TaskResult,
    TaskStatus,
)


class QueueConsumer(Protocol):
    def pull(self) -> tuple[QueueLease, ...]: ...

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None: ...


class CallbackSink(Protocol):
    def deliver(self, callback: TaskCallback) -> None: ...


class TaskExecutor(Protocol):
    def execute(self, task: MarketingTask) -> TaskResult: ...


@dataclass(frozen=True, slots=True)
class MarketingBridge:
    queue: QueueConsumer
    callbacks: CallbackSink
    inbox: MarketingInbox
    executor: TaskExecutor

    def recover(self) -> int:
        return self.inbox.recover_running()

    def tick(self) -> bool:
        try:
            leases = self.queue.pull()
        except CloudflareQueueError:
            leases = ()
        ack: list[str] = []
        retry: list[str] = []
        for lease in leases:
            try:
                _ = self.inbox.ingest(lease.task)
            except InboxConflictError:
                retry.append(lease.lease_id)
            else:
                ack.append(lease.lease_id)
        if ack or retry:
            with suppress(CloudflareQueueError):
                self.queue.acknowledge(
                    ack_lease_ids=tuple(ack),
                    retry_lease_ids=tuple(retry),
                )

        task = self.inbox.claim_next()
        if task is not None:
            _ = self.inbox.complete(task, self._execute(task))
        delivered = self._flush_callbacks()
        return bool(leases or task is not None or delivered)

    def _execute(self, task: MarketingTask) -> TaskResult:
        try:
            return self.executor.execute(task)
        except MarketingExecutionError as error:
            status = (
                TaskStatus.UNKNOWN_SIDE_EFFECT if error.unknown_side_effect else TaskStatus.FAILED
            )
            return TaskResult(status=status, failure_code=error.failure_code)
        except Exception:  # noqa: BLE001 - worker boundary converts unexpected failures.
            return TaskResult(status=TaskStatus.FAILED, failure_code="unexpected_worker_error")

    def _flush_callbacks(self) -> int:
        delivered = 0
        for callback in self.inbox.pending_callbacks():
            self.inbox.record_callback_attempt(callback.callback_id)
            try:
                self.callbacks.deliver(callback)
            except CloudflareQueueError:
                continue
            self.inbox.mark_callback_delivered(callback.callback_id)
            delivered += 1
        return delivered
