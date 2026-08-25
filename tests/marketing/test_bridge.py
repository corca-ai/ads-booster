from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from trace_capture.marketing.bridge import MarketingBridge
from trace_capture.marketing.executors import ArtifactSimulationExecutor
from trace_capture.marketing.inbox import MarketingInbox
from trace_capture.marketing.models import (
    MarketingTask,
    QueueLease,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)


def _task() -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_kr",
        kind=TaskKind.RESEARCH,
        idempotency_key="run-1:research:once",
        payload={"country": "KR"},
        created_at=datetime.now(UTC),
    )


@dataclass
class FakeQueue:
    leases: tuple[QueueLease, ...]
    acks: list[tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)

    def pull(self) -> tuple[QueueLease, ...]:
        leases, self.leases = self.leases, ()
        return leases

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None:
        self.acks.append((ack_lease_ids, retry_lease_ids))


@dataclass
class FakeCallbacks:
    delivered: list[TaskCallback] = field(default_factory=list)

    def deliver(self, callback: TaskCallback) -> None:
        self.delivered.append(callback)


class FakeExecutor:
    def execute(self, task: MarketingTask) -> TaskResult:
        return TaskResult(status=TaskStatus.SUCCEEDED, output={"task_id": task.task_id})


def test_bridge_persists_before_ack_and_delivers_idempotent_callback(tmp_path: Path) -> None:
    task = _task()
    queue = FakeQueue(
        (
            QueueLease(
                message_id="message-1",
                lease_id="lease-1",
                attempts=1,
                task=task,
            ),
        )
    )
    callbacks = FakeCallbacks()
    inbox = MarketingInbox(tmp_path)
    bridge = MarketingBridge(queue, callbacks, inbox, FakeExecutor())

    assert bridge.tick()
    assert queue.acks == [(("lease-1",), ())]
    assert [callback.callback_id for callback in callbacks.delivered] == ["task-1:completed"]
    assert inbox.pending_callbacks() == ()


def test_bridge_recovers_a_claimed_task_after_restart(tmp_path: Path) -> None:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    assert inbox.ingest(task)
    assert inbox.claim_next() == task

    assert inbox.recover_running() == 1
    assert inbox.claim_next() == task


def test_simulation_executor_emits_metrics_for_feedback_loop(tmp_path: Path) -> None:
    task = _task().model_copy(update={"kind": TaskKind.SAMPLE_METRICS, "payload": {"minute": 30}})

    result = ArtifactSimulationExecutor(tmp_path).execute(task)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["minute"] == 30
    assert isinstance(result.output["views"], int)
    assert result.artifacts[0].sha256
