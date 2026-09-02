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
    WorkerTaskEventType,
)


def _event_for_status(status: TaskStatus) -> WorkerTaskEventType:
    match status:
        case TaskStatus.SUCCEEDED:
            return WorkerTaskEventType.EXECUTION_SUCCEEDED
        case TaskStatus.FAILED:
            return WorkerTaskEventType.EXECUTION_FAILED
        case TaskStatus.UNKNOWN_SIDE_EFFECT:
            return WorkerTaskEventType.EXECUTION_UNKNOWN


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

    def report_event(
        self,
        task_id: str,
        event_type: WorkerTaskEventType,
        failure_code: str | None = None,
    ) -> None: ...

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
        self._report_event(task, WorkerTaskEventType.PREPARATION_STARTED)
        try:
            prepared = self.preparer.prepare(task)
        except MarketingExecutionError as error:
            result = self._known_failure(error)
            self._report_event(
                task,
                WorkerTaskEventType.PREPARATION_FAILED,
                result.failure_code,
            )
            _ = self.inbox.complete(task, result)
            return
        except Exception:  # noqa: BLE001, RUF100  # noqa: BROAD_EXCEPT_OK
            # The pre-admission boundary fail-closes an unexpected preparer crash as local failure.
            result = TaskResult(
                status=TaskStatus.FAILED,
                failure_code="unexpected_worker_error",
            )
            self._report_event(
                task,
                WorkerTaskEventType.PREPARATION_FAILED,
                result.failure_code,
            )
            _ = self.inbox.complete(task, result)
            return

        self.inbox.begin_execution(task.task_id, prepared.execution_admission)
        try:
            self.broker.mark_execution_started(task.task_id)
        except CloudflareQueueError:
            result = self._unknown_side_effect()
            self._report_result(task, result)
            _ = self.inbox.complete(task, result)
            return

        try:
            result = self.executor.execute(prepared)
        except MarketingExecutionError as error:
            result = self._known_failure(error)
        except Exception:  # noqa: BLE001, RUF100  # noqa: BROAD_EXCEPT_OK
            # The post-admission boundary never retries unknown native side effects after a crash.
            result = self._unknown_side_effect()
        self._report_result(task, result)
        _ = self.inbox.complete(task, result)

    def _report_result(self, task: MarketingTask, result: TaskResult) -> None:
        self._report_event(task, _event_for_status(result.status), result.failure_code)

    def _report_event(
        self,
        task: MarketingTask,
        event_type: WorkerTaskEventType,
        failure_code: str | None = None,
    ) -> None:
        with suppress(CloudflareQueueError):
            self.broker.report_event(task.task_id, event_type, failure_code)

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
    def _unknown_side_effect() -> TaskResult:
        return TaskResult(
            status=TaskStatus.UNKNOWN_SIDE_EFFECT,
            failure_code="native_appium_side_effect_unknown",
        )
