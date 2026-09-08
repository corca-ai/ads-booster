"""Authenticated preparation packets; this API contains no external executor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field, ValidationError

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.marketing_delivery import DeliveryProposal, ReviewTarget
from ads_booster.contracts.models import ContractModel, Identifier
from ads_booster.marketing.agent_service.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore

if TYPE_CHECKING:
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.marketing.agent_service.oauth import OAuthIdentity
    from ads_booster.transport.json_types import JsonObject


_COLLECTION_PARTS = 4
_ITEM_PARTS = 5


class PrepareDelivery(ContractModel):
    proposal_id: Identifier
    rationale: str = Field(min_length=1, max_length=4000)
    target: ReviewTarget
    expected_revision: int = Field(default=0, ge=0)


def dispatch_delivery(  # noqa: PLR0911 - explicit authenticated route responses.
    method: str,
    path: str,
    body: bytes,
    *,
    identity: OAuthIdentity,
    service: MarketingAgentService,
) -> tuple[int, JsonObject] | None:
    pieces = path.strip("/").split("/")
    if len(pieces) not in {4, 5} or pieces[:2] != ["v1", "runs"] or pieces[3] != "delivery":
        return None
    run_id = pieces[2]
    if service.repository.get(identity.tenant_id, run_id) is None:
        return 404, {"error": "agent_run_not_found"}
    scope = CreativeScope(workspace_id=identity.tenant_id, product_id="trace")
    store = DeliveryReviewStore(
        service.repository.database_path,
        asset_verifier=CreativeAssetVerifier(
            SqliteCreativeAssetRepository(
                service.repository.database_path,
                service.repository.database_path.parent / "artifacts",
            )
        ),
    )
    try:
        if method == "POST" and len(pieces) == _COLLECTION_PARTS:
            request = PrepareDelivery.model_validate_json(body)
            proposal = DeliveryProposal(
                proposal_id=request.proposal_id,
                scope=scope,
                run_id=run_id,
                rationale=request.rationale,
                target=request.target,
            )
            previous = store.get(scope, request.proposal_id)
            if previous is not None and previous.proposal.run_id != run_id:
                return 409, {"error": "delivery_run_binding_conflict"}
            packet = store.prepare(
                proposal, actor_scope=scope, expected_revision=request.expected_revision
            )
            return 201, packet.model_dump(mode="json")
        if method == "GET" and len(pieces) == _ITEM_PARTS:
            packet = store.get(scope, pieces[4])
            if packet is None or packet.proposal.run_id != run_id:
                return 404, {"error": "delivery_proposal_not_found"}
            return 200, packet.model_dump(mode="json")
        return 405, {"error": "delivery_operation_not_allowed"}  # noqa: TRY300 - route fallthrough.
    except ValidationError:
        return 400, {"error": "delivery_request_invalid"}
    except ValueError:
        return 409, {"error": "delivery_request_conflict"}
