from __future__ import annotations

from typing import TYPE_CHECKING, final, override

if TYPE_CHECKING:
    from ads_booster.automation.campaign_models import CampaignId
    from ads_booster.automation.models import QueueId, QueueState
    from ads_booster.workspace import WorkspaceId


@final
class InvalidQueueCompletionError(RuntimeError):
    def __init__(self, state: QueueState) -> None:
        """Preserve the rejected completion state for API callers."""
        self.state = state
        super().__init__(state)

    @override
    def __str__(self) -> str:
        return f"illegal queue completion state: {self.state}"


@final
class DuplicateIdempotencyError(RuntimeError):
    def __init__(self, idempotency_key: str) -> None:
        """Preserve the conflicting idempotency key for callers."""
        self.idempotency_key = idempotency_key
        super().__init__(idempotency_key)

    @override
    def __str__(self) -> str:
        return f"queue idempotency key conflicts with another payload: {self.idempotency_key}"


@final
class QueueNotFoundError(RuntimeError):
    def __init__(self, queue_id: QueueId) -> None:
        """Preserve the missing queue identifier for callers."""
        self.queue_id = queue_id
        super().__init__(queue_id)

    @override
    def __str__(self) -> str:
        return f"queue record was not found: {self.queue_id}"


@final
class QueueRevisionError(RuntimeError):
    def __init__(self, queue_id: QueueId, expected_revision: int) -> None:
        """Preserve the optimistic-concurrency conflict details."""
        self.queue_id = queue_id
        self.expected_revision = expected_revision
        super().__init__(queue_id, expected_revision)

    @override
    def __str__(self) -> str:
        return f"queue revision conflict: {self.queue_id}@{self.expected_revision}"


@final
class CampaignNotFoundError(RuntimeError):
    def __init__(self, workspace_id: WorkspaceId, campaign_id: CampaignId) -> None:
        """Preserve the scoped missing campaign identifiers for API callers."""
        self.workspace_id = workspace_id
        self.campaign_id = campaign_id
        super().__init__(workspace_id, campaign_id)

    @override
    def __str__(self) -> str:
        return f"campaign was not found: {self.workspace_id}/{self.campaign_id}"


@final
class CampaignRevisionError(RuntimeError):
    def __init__(self, campaign_id: CampaignId, expected_revision: int) -> None:
        """Preserve the rejected campaign revision for optimistic concurrency."""
        self.campaign_id = campaign_id
        self.expected_revision = expected_revision
        super().__init__(campaign_id, expected_revision)

    @override
    def __str__(self) -> str:
        return f"campaign revision conflict: {self.campaign_id}@{self.expected_revision}"
