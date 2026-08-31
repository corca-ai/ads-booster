from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from ads_booster.marketing.errors import CloudflareQueueError
from ads_booster.marketing.inbox import (
    ExecutionAdmission,
    InboxConflictError,
    MarketingExecutionError,
    MarketingInbox,
)
from ads_booster.marketing.models import (
    MarketingTask,
    QueueLease,
    TaskCallback,
    TaskResult,
    TaskStatus,
    task_unknown_side_effect_code,
)


class PreparedTask(Protocol):
    @property
    def execution_admission(self) -> ExecutionAdmission: ...


class TaskPreparer[TPrepared: PreparedTask](Protocol):
    def prepare(self, task: MarketingTask) -> TPrepared: ...


class TaskExecutor[TPrepared: PreparedTask](Protocol):
    def execute(self, prepared: TPrepared) -> TaskResult: ...


class WorkerBroker(Protocol):
    def pull(self) -> tuple[QueueLease, ...]: ...

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None: ...

    def mark_execution_started(self, task_id: str) -> None: ...

    def deliver(self, callback: TaskCallback) -> None: ...


@dataclass(frozen=True, slots=True)
class MarketingWorkerLoop[TPrepared: PreparedTask]:
    broker: WorkerBroker
    inbox: MarketingInbox
    preparer: TaskPreparer[TPrepared]
    executor: TaskExecutor[TPrepared]

    def recover(self) -> int:
        recovered = self.inbox.recover_interrupted()
        return recovered.requeued + recovered.unknown_side_effects

    def tick(self, *, accept_remote: bool = True) -> bool:
        leases = self._pull() if accept_remote else ()
        self._ingest_and_acknowledge(leases)

        task = self.inbox.claim_next()
        if task is not None:
            self._run(task)
        delivered = self._flush_callbacks()
        return bool(leases or task is not None or delivered)

    def _pull(self) -> tuple[QueueLease, ...]:
        try:
            return self.broker.pull()
        except CloudflareQueueError:
            return ()

    def _ingest_and_acknowledge(self, leases: tuple[QueueLease, ...]) -> None:
        acknowledged: list[str] = []
        retried: list[str] = []
        for lease in leases:
            try:
                _ = self.inbox.ingest(lease.task)
            except InboxConflictError:
                retried.append(lease.lease_id)
            else:
                acknowledged.append(lease.lease_id)
        if acknowledged or retried:
            with suppress(CloudflareQueueError):
                self.broker.acknowledge(
                    ack_lease_ids=tuple(acknowledged),
                    retry_lease_ids=tuple(retried),
                )

    def _run(self, task: MarketingTask) -> None:
        try:
            prepared = self.preparer.prepare(task)
        except MarketingExecutionError as error:
            _ = self.inbox.complete(task, self._known_failure(error))
            return
        except Exception:  # noqa: BLE001, RUF100  # noqa: BROAD_EXCEPT_OK
            # The pre-admission boundary fail-closes an unexpected preparer crash as local failure.
            _ = self.inbox.complete(
                task,
                TaskResult(status=TaskStatus.FAILED, failure_code="unexpected_worker_error"),
            )
            return

        self.inbox.begin_execution(task.task_id, prepared.execution_admission)
        try:
            self.broker.mark_execution_started(task.task_id)
        except CloudflareQueueError:
            _ = self.inbox.complete(task, self._unknown_side_effect(task))
            return

        try:
            result = self.executor.execute(prepared)
        except MarketingExecutionError as error:
            result = self._known_failure(error)
        except Exception:  # noqa: BLE001, RUF100  # noqa: BROAD_EXCEPT_OK
            # The post-admission boundary never replays a possibly spent Codex or native action.
            result = self._unknown_side_effect(task)
        _ = self.inbox.complete(task, result)

    def _flush_callbacks(self) -> int:
        delivered = 0
        for callback in self.inbox.pending_callbacks():
            self.inbox.record_callback_attempt(callback.callback_id)
            try:
                self.broker.deliver(callback)
            except CloudflareQueueError:
                continue
            self.inbox.mark_callback_delivered(callback.callback_id)
            delivered += 1
        return delivered

    @staticmethod
    def _known_failure(error: MarketingExecutionError) -> TaskResult:
        status = TaskStatus.UNKNOWN_SIDE_EFFECT if error.unknown_side_effect else TaskStatus.FAILED
        return TaskResult(status=status, failure_code=error.failure_code)

    @staticmethod
    def _unknown_side_effect(task: MarketingTask) -> TaskResult:
        return TaskResult(
            status=TaskStatus.UNKNOWN_SIDE_EFFECT,
            failure_code=task_unknown_side_effect_code(task.kind),
        )
