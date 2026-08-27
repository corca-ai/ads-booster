from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from typing import TYPE_CHECKING, final

import pytest
from pydantic import ValidationError

from ads_booster.automation import (
    AutomationQueue,
    DuplicateIdempotencyError,
    GenerateOneWorker,
    QueueRevisionError,
    QueueScheduler,
    QueueState,
    QueueSubmission,
)
from ads_booster.automation.models import QueueCompletion
from ads_booster.contracts import TraceRunResult
from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.contracts.run import TraceRunState
from ads_booster.workspace import WorkspaceId

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


def _bundle(request_id: str = "queue-request") -> MarketingContextBundle:
    return MarketingContextBundle.model_validate(
        {
            "schema_version": "trace.marketing-context.v1",
            "request_id": request_id,
            "persona": {
                "persona_id": "student",
                "country": "JP",
                "locale": "ja-JP",
                "age_group": "18-24",
                "occupation": "student",
                "traits": ["focused"],
                "interests": ["study"],
            },
            "promotion_material": {
                "promotion_material_id": "exam",
                "feature": "countdown",
                "concept": "exam preparation",
                "tone": ["calm"],
            },
            "reference_date": NOW.isoformat(),
            "device": {
                "kind": "simulator",
                "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
                "platform_version": "26.5",
                "device_name": "iPhone 17 Pro",
            },
        }
    )


def _submission(*, request_id: str = "queue-request", due_at: datetime = NOW) -> QueueSubmission:
    return QueueSubmission(
        workspace_id=WorkspaceId("workspace-1"),
        idempotency_key=f"enqueue-{request_id}",
        bundle=_bundle(request_id),
        due_at=due_at,
        max_attempts=3,
    )


@final
class FixtureRunner:
    def __init__(self, output_root: Path, *, missing_output: bool = False) -> None:
        self.output_root = output_root
        self.missing_output = missing_output
        self.calls = 0

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        self.calls += 1
        job_root = self.output_root / bundle.request_id
        component = job_root / "work" / "trace-components.png"
        output = job_root / "outputs" / "final.png"
        component.parent.mkdir(parents=True, exist_ok=True)
        _ = component.write_bytes(b"fixture-component")
        if not self.missing_output:
            output.parent.mkdir(parents=True, exist_ok=True)
            _ = output.write_bytes(b"fixture-output")
        return TraceRunResult(
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v1",
            input_digest="1" * 64,
            state=TraceRunState.COMPLETED,
            component_artifact="work/trace-components.png",
            component_artifact_sha256=sha256(b"fixture-component").hexdigest(),
            output_image="outputs/final.png",
            output_image_sha256=sha256(b"fixture-output").hexdigest(),
        )


@final
class InterruptingRunner:
    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        del bundle
        raise KeyboardInterrupt


def test_manual_enqueue_persists_and_scheduler_selects_only_due_work(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    future = queue.enqueue(_submission(request_id="future", due_at=NOW + timedelta(minutes=1)))
    due = queue.enqueue(_submission(request_id="due"))

    claimed = QueueScheduler(queue, worker_id="worker-1", lease_seconds=30).poll(NOW)

    assert claimed is not None
    assert (claimed.queue_id, claimed.state, claimed.attempts) == (
        due.queue_id,
        QueueState.CLAIMED,
        1,
    )
    assert AutomationQueue(tmp_path).get(future.workspace_id, future.queue_id) == future


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    _ = queue.enqueue(_submission())
    barrier = Barrier(2)

    def claim(worker_id: str) -> str | None:
        _ = barrier.wait()
        record = AutomationQueue(tmp_path).claim_due(worker_id=worker_id, now=NOW, lease_seconds=30)
        return None if record is None else record.queue_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(claim, ("worker-1", "worker-2")))

    assert sum(result is not None for result in results) == 1


def test_expired_claim_is_safely_reclaimed_but_running_attempt_is_not(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    submitted = queue.enqueue(_submission())
    first = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=10)
    assert first is not None
    reclaimed = queue.claim_due(
        worker_id="worker-2", now=NOW + timedelta(seconds=11), lease_seconds=10
    )
    assert reclaimed is not None
    assert (reclaimed.queue_id, reclaimed.attempts) == (submitted.queue_id, 2)
    running = queue.start(
        reclaimed.queue_id,
        worker_id="worker-2",
        expected_revision=reclaimed.revision,
        now=NOW + timedelta(seconds=12),
    )

    loser = queue.claim_due(worker_id="worker-3", now=NOW + timedelta(seconds=23), lease_seconds=10)

    assert loser is None
    recovered = queue.get(running.workspace_id, running.queue_id)
    assert (recovered.state, recovered.failure_code) == (
        QueueState.FAILED,
        "unknown_side_effect",
    )


def test_worker_links_completed_fixture_artifact_then_accepts_review(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path / "home")
    _ = queue.enqueue(_submission())
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=30)
    assert claimed is not None
    runner = FixtureRunner(tmp_path / "generated")

    review = GenerateOneWorker(queue, runner, runner.output_root).run_claim(
        claimed, now=NOW + timedelta(seconds=1)
    )
    accepted = queue.review(
        review.queue_id,
        workspace_id=review.workspace_id,
        accepted=True,
        expected_revision=review.revision,
        now=NOW + timedelta(seconds=2),
    )

    assert runner.calls == 1
    assert (review.state, review.run_id, review.run_idempotency_key) == (
        QueueState.REVIEW,
        "queue-request",
        "queue-request-v1",
    )
    assert review.artifact_path == "outputs/final.png"
    assert review.artifact_sha256 == sha256(b"fixture-output").hexdigest()
    assert accepted.state is QueueState.ACCEPTED


def test_rejected_review_requeues_the_same_goal_without_stale_artifact_claims(
    tmp_path: Path,
) -> None:
    # Given a queue record whose first generated artifact awaits review
    queue = AutomationQueue(tmp_path / "home")
    _ = queue.enqueue(_submission(request_id="replan-request"))
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=30)
    assert claimed is not None
    runner = FixtureRunner(tmp_path / "generated")
    review = GenerateOneWorker(queue, runner, runner.output_root).run_claim(
        claimed, now=NOW + timedelta(seconds=1)
    )

    # When the reviewer rejects that artifact
    replanning = queue.review(
        review.queue_id,
        workspace_id=review.workspace_id,
        accepted=False,
        expected_revision=review.revision,
        now=NOW + timedelta(seconds=2),
    )

    # Then the same queue item is runnable again without claiming the rejected output
    assert replanning.state is QueueState.SUBMITTED
    assert replanning.attempts == 0
    assert replanning.run_id is None
    assert replanning.artifact_path is None
    assert replanning.artifact_sha256 is None


def test_duplicate_idempotency_replays_same_payload_and_rejects_conflict(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    first = queue.enqueue(_submission())

    assert queue.enqueue(_submission()) == first
    with pytest.raises(DuplicateIdempotencyError):
        _ = queue.enqueue(
            _submission(request_id="different").model_copy(
                update={"idempotency_key": first.idempotency_key}
            )
        )


def test_stale_revision_and_malformed_payload_fail_closed(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    claimed = (
        queue.claim_due(
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        if queue.enqueue(_submission())
        else None
    )
    assert claimed is not None

    with pytest.raises(QueueRevisionError):
        _ = queue.start(
            claimed.queue_id,
            worker_id="worker-1",
            expected_revision=claimed.revision - 1,
            now=NOW,
        )
    with pytest.raises(ValidationError):
        _ = QueueSubmission.model_validate({"workspace_id": "workspace-1"})


def test_interrupted_running_attempt_becomes_unknown_and_is_not_retried(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    queued = queue.enqueue(_submission())
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=5)
    assert claimed is not None

    with pytest.raises(KeyboardInterrupt):
        _ = GenerateOneWorker(queue, InterruptingRunner(), tmp_path).run_claim(claimed, now=NOW)
    assert (
        queue.claim_due(worker_id="worker-2", now=NOW + timedelta(seconds=6), lease_seconds=5)
        is None
    )
    failed = queue.get(queued.workspace_id, queued.queue_id)
    assert (failed.state, failed.attempts, failed.failure_code) == (
        QueueState.FAILED,
        1,
        "unknown_side_effect",
    )


def test_misleading_completed_result_without_artifact_fails_review_gate(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path / "home")
    _ = queue.enqueue(_submission())
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=30)
    assert claimed is not None
    runner = FixtureRunner(tmp_path / "generated", missing_output=True)

    failed = GenerateOneWorker(queue, runner, runner.output_root).run_claim(claimed, now=NOW)

    assert (failed.state, failed.failure_code) == (QueueState.FAILED, "artifact_unverified")


@pytest.mark.parametrize("illegal_state", [QueueState.ACCEPTED, QueueState.SUBMITTED])
def test_finish_rejects_illegal_state_without_releasing_running_work(
    tmp_path: Path, illegal_state: QueueState
) -> None:
    queue = AutomationQueue(tmp_path)
    queued = queue.enqueue(_submission(request_id=illegal_state))
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=5)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id, worker_id="worker-1", expected_revision=claimed.revision, now=NOW
    )

    with pytest.raises(RuntimeError, match="illegal queue completion state"):
        _ = queue.finish(running, completion=QueueCompletion(state=illegal_state), now=NOW)

    assert queue.get(queued.workspace_id, queued.queue_id).state is QueueState.RUNNING
    assert (
        queue.claim_due(worker_id="worker-2", now=NOW + timedelta(seconds=6), lease_seconds=5)
        is None
    )


def test_running_lease_expiring_exactly_now_is_sealed_unknown(tmp_path: Path) -> None:
    queue = AutomationQueue(tmp_path)
    _ = queue.enqueue(_submission())
    claimed = queue.claim_due(worker_id="worker-1", now=NOW, lease_seconds=5)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id, worker_id="worker-1", expected_revision=claimed.revision, now=NOW
    )

    retry = queue.claim_due(worker_id="worker-2", now=NOW + timedelta(seconds=5), lease_seconds=5)

    recovered = queue.get(running.workspace_id, running.queue_id)
    assert retry is None
    assert (recovered.state, recovered.failure_code) == (QueueState.FAILED, "unknown_side_effect")
