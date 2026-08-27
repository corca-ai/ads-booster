from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from ads_booster.automation.errors import CampaignRevisionError
from ads_booster.automation.models import QueueId, QueueState, QueueSubmission
from ads_booster.contracts.generation import MarketingContextBundle

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.automation.campaign_models import CampaignRecord
    from ads_booster.automation.campaign_store import CampaignStore
    from ads_booster.automation.models import QueueRecord
    from ads_booster.automation.store import AutomationQueue


@dataclass(frozen=True, slots=True)
class CampaignProducer:
    campaigns: CampaignStore
    queue: AutomationQueue

    def tick(self, now: datetime) -> QueueRecord | None:
        for campaign in self.campaigns.list_active():
            match self._current_state(campaign):
                case None | QueueState.REVIEW | QueueState.ACCEPTED | QueueState.REJECTED:
                    pass
                case QueueState.SUBMITTED | QueueState.CLAIMED | QueueState.RUNNING:
                    continue
                case QueueState.FAILED:
                    _ = self.campaigns.stop(
                        campaign.workspace_id,
                        campaign.campaign_id,
                        expected_revision=campaign.revision,
                    )
                    continue
                case unreachable:
                    assert_never(unreachable)
            if (
                campaign.variation_count is not None
                and campaign.next_variation >= campaign.variation_count
            ):
                _ = self.campaigns.complete(
                    campaign.campaign_id,
                    expected_revision=campaign.revision,
                )
                continue
            queued = self.queue.enqueue(
                QueueSubmission(
                    workspace_id=campaign.workspace_id,
                    idempotency_key=_request_id(campaign),
                    bundle=_bundle(campaign),
                    due_at=now,
                )
            )
            try:
                _ = self.campaigns.mark_enqueued(
                    campaign.campaign_id,
                    queue_id=queued.queue_id,
                    expected_revision=campaign.revision,
                )
            except CampaignRevisionError:
                return queued
            return queued
        return None

    def _current_state(self, campaign: CampaignRecord) -> QueueState | None:
        if campaign.current_queue_id is None:
            return None
        return self.queue.get(
            campaign.workspace_id,
            QueueId(campaign.current_queue_id),
        ).state


def _request_id(campaign: CampaignRecord) -> str:
    return f"campaign-{campaign.campaign_id}-{campaign.next_variation:06d}"


def _bundle(campaign: CampaignRecord) -> MarketingContextBundle:
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=_request_id(campaign),
        campaign_id=campaign.campaign_id,
        variation_index=campaign.next_variation,
        persona=campaign.persona,
        promotion_material=campaign.promotion_material,
        reference_images=campaign.reference_images,
        reference_date=campaign.reference_date,
        device=campaign.device,
    )
