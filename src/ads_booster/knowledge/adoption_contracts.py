from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001 -- Pydantic runtime type
from ads_booster.knowledge.contract_types import KnowledgeContractModel, UtcDatetime

if TYPE_CHECKING:
    from ads_booster.knowledge.scope_contracts import ActorContext


class ExplicitAdoptionReceipt(KnowledgeContractModel):
    receipt_id: BoundedId
    proposal_id: BoundedId
    question_id: BoundedId
    workspace_id: BoundedId
    actor_ref: BoundedId
    brand_id: BoundedId
    expected_revision_id: BoundedId
    authenticated_event_id: BoundedId
    answered_at: UtcDatetime


class AdoptionReceiptResolver(Protocol):
    def adoption_receipt(
        self,
        actor: ActorContext,
        receipt_id: str,
    ) -> ExplicitAdoptionReceipt | None: ...


__all__ = ["AdoptionReceiptResolver", "ExplicitAdoptionReceipt"]
