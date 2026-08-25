from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from trace_capture.automation import (
    AutomationQueue,
    CampaignCreate,
    CampaignProducer,
    CampaignState,
    CampaignStore,
    QueueState,
)
from trace_capture.automation.models import QueueCompletion
from trace_capture.contracts.generation import PersonaProfile, PromotionMaterial
from trace_capture.contracts.models import DeviceKind, DeviceTarget
from trace_capture.workspace import WorkspaceId

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)


def _campaign() -> CampaignCreate:
    return CampaignCreate(
        workspace_id=WorkspaceId("workspace-1"),
        name="Exam week variations",
        persona=PersonaProfile(
            persona_id="student",
            country="JP",
            locale="ja-JP",
            age_group="20s",
            occupation="university_student",
            traits=("focused",),
            interests=("study",),
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id="exam-week",
            feature="lock_screen_schedule",
            concept="exam_week",
            tone=("calm",),
            trace_items=("統計学 2限", "レポート提出", "ゼミ準備"),
        ),
        reference_date=NOW,
        device=DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
    )


def test_continuous_campaign_when_service_restarts_then_it_enqueues_next_variation(
    tmp_path: Path,
) -> None:
    # Given an active continuous campaign with its first durable queue item
    campaigns = CampaignStore(tmp_path)
    campaign = campaigns.create(_campaign())
    queue = AutomationQueue(tmp_path)
    first = CampaignProducer(campaigns, queue).tick(NOW)
    assert first is not None
    assert first.bundle.variation_index == 0
    assert CampaignProducer(campaigns, queue).tick(NOW) is None
    claimed = queue.claim_due(worker_id="worker", now=NOW, lease_seconds=30)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id,
        worker_id="worker",
        expected_revision=claimed.revision,
        now=NOW + timedelta(seconds=1),
    )
    _ = queue.finish(
        running,
        completion=QueueCompletion(state=QueueState.REVIEW),
        now=NOW + timedelta(seconds=2),
    )

    # When a restarted producer ticks after the prior variation reaches a terminal state
    restarted_campaigns = CampaignStore(tmp_path)
    second = CampaignProducer(restarted_campaigns, AutomationQueue(tmp_path)).tick(
        NOW + timedelta(seconds=3)
    )

    # Then it emits exactly the next variation and retains the frozen campaign inputs
    assert second is not None
    assert second.bundle.variation_index == 1
    assert second.bundle.persona == campaign.persona
    assert second.bundle.promotion_material == campaign.promotion_material
    persisted = restarted_campaigns.list_workspace(campaign.workspace_id)[0]
    assert persisted.next_variation == 2
    assert persisted.current_queue_id == second.queue_id


def test_stopped_campaign_when_current_work_finishes_then_it_does_not_enqueue_more(
    tmp_path: Path,
) -> None:
    # Given a campaign whose future production has been stopped
    campaigns = CampaignStore(tmp_path)
    campaign = campaigns.create(_campaign())
    stopped = campaigns.stop(
        campaign.workspace_id,
        campaign.campaign_id,
        expected_revision=campaign.revision,
    )

    # When the producer checks for work
    queued = CampaignProducer(campaigns, AutomationQueue(tmp_path)).tick(NOW)

    # Then no new variation is created and the durable state remains stopped
    assert queued is None
    assert stopped.state is CampaignState.STOPPED


def test_finite_campaign_when_requested_count_is_reached_then_it_completes(
    tmp_path: Path,
) -> None:
    # Given a one-variation campaign whose only queue item has finished
    campaigns = CampaignStore(tmp_path)
    campaign = campaigns.create(_campaign().model_copy(update={"variation_count": 1}))
    queue = AutomationQueue(tmp_path)
    producer = CampaignProducer(campaigns, queue)
    first = producer.tick(NOW)
    assert first is not None
    claimed = queue.claim_due(worker_id="worker", now=NOW, lease_seconds=30)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id,
        worker_id="worker",
        expected_revision=claimed.revision,
        now=NOW + timedelta(seconds=1),
    )
    _ = queue.finish(
        running,
        completion=QueueCompletion(state=QueueState.REVIEW),
        now=NOW + timedelta(seconds=2),
    )

    # When the producer checks for another variation
    queued = producer.tick(NOW + timedelta(seconds=3))

    # Then it creates no extra work and persists the completed state
    assert queued is None
    persisted = campaigns.list_workspace(campaign.workspace_id)[0]
    assert persisted.state is CampaignState.COMPLETED
    assert persisted.next_variation == 1


def test_continuous_campaign_when_generation_fails_then_it_stops_future_work(
    tmp_path: Path,
) -> None:
    # Given a continuous campaign whose current generation failed
    campaigns = CampaignStore(tmp_path)
    campaign = campaigns.create(_campaign())
    queue = AutomationQueue(tmp_path)
    producer = CampaignProducer(campaigns, queue)
    first = producer.tick(NOW)
    assert first is not None
    claimed = queue.claim_due(worker_id="worker", now=NOW, lease_seconds=30)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id,
        worker_id="worker",
        expected_revision=claimed.revision,
        now=NOW + timedelta(seconds=1),
    )
    _ = queue.finish(
        running,
        completion=QueueCompletion(state=QueueState.FAILED, failure_code="fixture"),
        now=NOW + timedelta(seconds=2),
    )

    # When the producer checks for the next variation
    queued = producer.tick(NOW + timedelta(seconds=3))

    # Then it fails closed instead of repeating the broken external operation
    assert queued is None
    persisted = campaigns.list_workspace(campaign.workspace_id)[0]
    assert persisted.state is CampaignState.STOPPED
    assert persisted.next_variation == 1
