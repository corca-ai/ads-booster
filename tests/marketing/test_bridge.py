from __future__ import annotations

# noqa: SIZE_OK -- bridge delivery and review scenarios share durable fakes
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import override

import pytest

from ads_booster.candidate_generation import CandidateBatch
from ads_booster.marketing.bridge import MarketingBridge
from ads_booster.marketing.cloudflare_queue import CloudflareQueueError
from ads_booster.marketing.executors import (
    ArtifactSimulationExecutor,
    CandidatePipelineExecutor,
)
from ads_booster.marketing.inbox import MarketingExecutionError, MarketingInbox
from ads_booster.marketing.models import (
    ApprovalDecision,
    ApprovalPhase,
    MarketingTask,
    QueueLease,
    ReviewApproval,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from ads_booster.workspace import (
    CandidateCreate,
    CandidateId,
    CandidateImageAttachment,
    CandidateImageInputs,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    SqliteWorkspaceStore,
    WorkspaceId,
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


@dataclass(slots=True)  # noqa: MUTABLE_OK
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


@dataclass(slots=True)  # noqa: MUTABLE_OK
class FakeCallbacks:
    delivered: list[TaskCallback] = field(default_factory=list)
    approvals: list[ReviewApproval] = field(default_factory=list)

    def deliver(self, callback: TaskCallback) -> None:
        self.delivered.append(callback)

    def deliver_approval(self, approval: ReviewApproval) -> None:
        self.approvals.append(approval)


@dataclass(slots=True)  # noqa: MUTABLE_OK
class FlakyApprovalCallbacks(FakeCallbacks):
    failures_remaining: int = 1

    @override
    def deliver_approval(self, approval: ReviewApproval) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            message = "review endpoint unavailable"
            raise CloudflareQueueError(message)
        super().deliver_approval(approval)


class FakeExecutor:
    def execute(self, task: MarketingTask) -> TaskResult:
        return TaskResult(status=TaskStatus.SUCCEEDED, output={"task_id": task.task_id})


class UnknownSideEffectExecutor:
    def execute(self, task: MarketingTask) -> TaskResult:
        _ = task
        failure_code = "native_appium_side_effect_unknown"
        raise MarketingExecutionError(
            failure_code,
            unknown_side_effect=True,
        )


@dataclass(slots=True)  # noqa: MUTABLE_OK
class FailingQueue:
    failure: str

    def pull(self) -> tuple[QueueLease, ...]:
        if self.failure == "pull":
            message = "pull unavailable"
            raise CloudflareQueueError(message)
        return (QueueLease(message_id="message-1", lease_id="lease-1", attempts=1, task=_task()),)

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None:
        _ = (ack_lease_ids, retry_lease_ids)
        if self.failure == "ack":
            message = "ack unavailable"
            raise CloudflareQueueError(message)


@dataclass(slots=True)  # noqa: MUTABLE_OK
class FakeCandidateGenerator:
    store: SqliteWorkspaceStore
    run_contexts: list[str | None] = field(default_factory=list)

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
    ) -> CandidateBatch:
        self.run_contexts.append(run_context)
        return CandidateBatch(
            records=(
                self.store.create_candidate(
                    CandidateCreate(
                        workspace_id=workspace_id,
                        source=CandidateSource.AUTO,
                        country="KR",
                        topic="시험기간 일정",
                        caption="잠금화면에서 오늘 일정을 확인합니다.",
                        hypothesis="구체적인 사용 장면이 반응을 만든다.",
                        image_inputs=CandidateImageInputs(
                            trace_items=("09:00 통계학", "13:00 스터디"),
                            device_time="07:20",
                            background_intent="늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방",
                            language="ko",
                        ),
                    )
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class FakeCandidateImageRunner:
    store: SqliteWorkspaceStore
    home: Path

    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        record = self.store.get_candidate(workspace_id, candidate_id)
        relative = Path("candidates") / str(candidate_id) / "final.png"
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = b"candidate-image"
        _ = path.write_bytes(content)
        return self.store.attach_candidate_image(
            workspace_id,
            candidate_id,
            CandidateImageAttachment(
                path=relative.as_posix(),
                sha256=sha256(content).hexdigest(),
                agent_run_id=f"candidate-{candidate_id}-r{record.revision}",
                expected_revision=record.revision,
            ),
        )


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


def test_drain_guard_stops_remote_claims_but_finishes_local_work(tmp_path: Path) -> None:
    class ForbiddenRemoteQueue:
        @staticmethod
        def pull() -> tuple[QueueLease, ...]:
            message = "remote pull must be disabled during update drain"
            raise AssertionError(message)

        @staticmethod
        def acknowledge(
            *,
            ack_lease_ids: tuple[str, ...] = (),
            retry_lease_ids: tuple[str, ...] = (),
        ) -> None:
            _ = (ack_lease_ids, retry_lease_ids)
            message = "no remote lease should be acknowledged during update drain"
            raise AssertionError(message)

    callbacks = FakeCallbacks()
    inbox = MarketingInbox(tmp_path)
    _ = inbox.ingest(_task())
    bridge = MarketingBridge(ForbiddenRemoteQueue(), callbacks, inbox, FakeExecutor())

    assert bridge.tick(accept_remote=False)
    assert [callback.callback_id for callback in callbacks.delivered] == ["task-1:completed"]
    assert inbox.quiescence().ready


def test_bridge_delivers_unknown_side_effect_without_collapsing_it(tmp_path: Path) -> None:
    task = _task()
    callbacks = FakeCallbacks()
    bridge = MarketingBridge(
        queue=FakeQueue(
            (QueueLease(message_id="message-1", lease_id="lease-1", attempts=1, task=task),)
        ),
        callbacks=callbacks,
        inbox=MarketingInbox(tmp_path),
        executor=UnknownSideEffectExecutor(),
    )

    assert bridge.tick()
    assert callbacks.delivered[0].result.status is TaskStatus.UNKNOWN_SIDE_EFFECT
    assert callbacks.delivered[0].result.failure_code == "native_appium_side_effect_unknown"


def test_bridge_recovers_a_claimed_task_after_restart(tmp_path: Path) -> None:
    inbox = MarketingInbox(tmp_path)
    task = _task()
    assert inbox.ingest(task)
    assert inbox.claim_next() == task

    assert inbox.recover_running() == 1
    assert inbox.claim_next() == task


@pytest.mark.parametrize("failure", ["pull", "ack"])
def test_bridge_keeps_durable_local_work_moving_during_queue_outage(
    tmp_path: Path,
    failure: str,
) -> None:
    inbox = MarketingInbox(tmp_path)
    if failure == "pull":
        assert inbox.ingest(_task())
    callbacks = FakeCallbacks()
    bridge = MarketingBridge(FailingQueue(failure), callbacks, inbox, FakeExecutor())

    assert bridge.tick()
    assert [callback.task_id for callback in callbacks.delivered] == ["task-1"]


def test_simulation_executor_emits_metrics_for_feedback_loop(tmp_path: Path) -> None:
    task = _task().model_copy(update={"kind": TaskKind.SAMPLE_METRICS, "payload": {"minute": 30}})

    result = ArtifactSimulationExecutor(tmp_path).execute(task)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["minute"] == 30
    assert isinstance(result.output["views"], int)
    assert result.artifacts[0].sha256


def test_candidate_pipeline_executor_obeys_both_review_gates(tmp_path: Path) -> None:
    home = tmp_path / "agent"
    store = SqliteWorkspaceStore(home)
    workspace_id = store.create_workspace("Trace team").workspace.workspace_id
    generator = FakeCandidateGenerator(store)
    executor = CandidatePipelineExecutor(
        generator=generator,
        image_runner=FakeCandidateImageRunner(store, home),
        store=store,
        artifact_root=home,
        fallback=ArtifactSimulationExecutor(home / "marketing-simulation"),
    )
    generated = executor.execute(
        _task().model_copy(
            update={
                "kind": TaskKind.GENERATE_CANDIDATES,
                "payload": {
                    "workspace_id": str(workspace_id),
                    "shared_instruction": {"body": "공통 지침"},
                    "private_memory": [],
                },
            }
        )
    )
    generated_ids = generated.output["candidate_ids"]
    assert isinstance(generated_ids, list)
    assert isinstance(generated_ids[0], str)
    candidate_id = CandidateId(generated_ids[0])
    assert generator.run_contexts
    assert "공통 지침" in str(generator.run_contexts[0])
    candidate = store.get_candidate(workspace_id, candidate_id)

    caption_approved = store.review_candidate(
        workspace_id,
        candidate_id,
        accepted=True,
        note=None,
        expected_revision=candidate.revision,
    )
    captured = executor.execute(
        _task().model_copy(
            update={
                "kind": TaskKind.CAPTURE,
                "payload": {
                    "workspace_id": str(workspace_id),
                    "candidate_ids": [str(candidate_id)],
                },
            }
        )
    )
    assert captured.output["quality"] == "awaiting_image_review"
    assert captured.artifacts[0].sha256
    image = store.get_candidate(workspace_id, candidate_id)
    assert image.status is CandidateStatus.IMAGE_AWAITING_REVIEW

    publish = _task().model_copy(
        update={
            "kind": TaskKind.PUBLISH,
            "payload": {
                "workspace_id": str(workspace_id),
                "candidate_ids": [str(candidate_id)],
                "adapter_mode": "simulation",
            },
        }
    )
    with pytest.raises(MarketingExecutionError, match="candidate_not_image_approved"):
        _ = executor.execute(publish)

    _ = store.review_candidate_image(
        workspace_id,
        candidate_id,
        accepted=True,
        note=None,
        expected_revision=image.revision,
    )
    published = executor.execute(publish)
    assert published.status is TaskStatus.SUCCEEDED
    assert str(published.output["publication_id"]).startswith("sim://threads/")
    assert caption_approved.status is CandidateStatus.CAPTION_APPROVED

    with pytest.raises(MarketingExecutionError, match="live_adapter_unavailable"):
        _ = executor.execute(
            publish.model_copy(update={"payload": {**publish.payload, "adapter_mode": "live"}})
        )


def test_bridge_relays_completed_workspace_reviews_without_manual_api_calls(tmp_path: Path) -> None:
    home = tmp_path / "agent"
    store = SqliteWorkspaceStore(home)
    workspace_id = store.create_workspace("Trace team").workspace.workspace_id
    generator = FakeCandidateGenerator(store)
    executor = CandidatePipelineExecutor(
        generator=generator,
        image_runner=FakeCandidateImageRunner(store, home),
        store=store,
        artifact_root=home,
        fallback=ArtifactSimulationExecutor(home / "marketing-simulation"),
    )
    generation = _task().model_copy(
        update={
            "kind": TaskKind.GENERATE_CANDIDATES,
            "payload": {"workspace_id": str(workspace_id)},
        }
    )
    queue = FakeQueue(
        (QueueLease(message_id="message-1", lease_id="lease-1", attempts=1, task=generation),)
    )
    callbacks = FakeCallbacks()
    bridge = MarketingBridge(
        queue=queue,
        callbacks=callbacks,
        inbox=MarketingInbox(home / "marketing-bridge"),
        executor=executor,
        review_store=store,
    )

    assert bridge.tick()
    generated_ids = callbacks.delivered[0].result.output["candidate_ids"]
    assert isinstance(generated_ids, list)
    candidate_id = CandidateId(str(generated_ids[0]))
    assert callbacks.approvals == []

    candidate = store.get_candidate(workspace_id, candidate_id)
    _ = store.review_candidate(
        workspace_id,
        candidate_id,
        accepted=True,
        note=None,
        expected_revision=candidate.revision,
    )
    assert bridge.tick()
    assert callbacks.approvals[0].phase is ApprovalPhase.CANDIDATES
    assert callbacks.approvals[0].decision is ApprovalDecision.APPROVED
    assert callbacks.approvals[0].candidate_ids == (str(candidate_id),)

    capture = generation.model_copy(
        update={
            "task_id": "task-2",
            "kind": TaskKind.CAPTURE,
            "payload": {
                "workspace_id": str(workspace_id),
                "candidate_ids": [str(candidate_id)],
            },
        }
    )
    queue.leases = (
        QueueLease(message_id="message-2", lease_id="lease-2", attempts=1, task=capture),
    )
    assert bridge.tick()
    assert len(callbacks.approvals) == 1

    image = store.get_candidate(workspace_id, candidate_id)
    _ = store.review_candidate_image(
        workspace_id,
        candidate_id,
        accepted=True,
        note=None,
        expected_revision=image.revision,
    )
    assert bridge.tick()
    assert callbacks.approvals[-1].phase is ApprovalPhase.PUBLICATION
    assert callbacks.approvals[-1].decision is ApprovalDecision.APPROVED


def test_rejected_review_event_survives_transport_failure(tmp_path: Path) -> None:
    home = tmp_path / "agent"
    store = SqliteWorkspaceStore(home)
    workspace_id = store.create_workspace("Trace team").workspace.workspace_id
    generation = _task().model_copy(
        update={
            "kind": TaskKind.GENERATE_CANDIDATES,
            "payload": {"workspace_id": str(workspace_id)},
        }
    )
    callbacks = FlakyApprovalCallbacks()
    bridge = MarketingBridge(
        queue=FakeQueue(
            (
                QueueLease(
                    message_id="message-1",
                    lease_id="lease-1",
                    attempts=1,
                    task=generation,
                ),
            )
        ),
        callbacks=callbacks,
        inbox=MarketingInbox(home / "marketing-bridge"),
        executor=CandidatePipelineExecutor(
            generator=FakeCandidateGenerator(store),
            image_runner=FakeCandidateImageRunner(store, home),
            store=store,
            artifact_root=home,
            fallback=ArtifactSimulationExecutor(home / "marketing-simulation"),
        ),
        review_store=store,
    )
    assert bridge.tick()
    generated_ids = callbacks.delivered[0].result.output["candidate_ids"]
    assert isinstance(generated_ids, list)
    candidate_id = CandidateId(str(generated_ids[0]))
    candidate = store.get_candidate(workspace_id, candidate_id)
    _ = store.review_candidate(
        workspace_id,
        candidate_id,
        accepted=False,
        note="다른 방향 필요",
        expected_revision=candidate.revision,
    )

    assert bridge.tick()
    assert callbacks.approvals == []
    assert bridge.tick()
    assert callbacks.approvals[0].decision is ApprovalDecision.REJECTED
    assert callbacks.approvals[0].candidate_ids == ()
