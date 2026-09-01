"""Safe, data-only projections for marketing planner contexts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    FeatureEvidencePacket,
    FeatureLifecycle,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest


class FeaturePlanningProjection(ContractModel):
    """Planner-safe product identity without claim text, sources, or other instructions."""

    schema_version: Literal["trace.feature-planning-projection.v1"]
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    lifecycle: FeatureLifecycle
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)]
    allowed_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)]

    @classmethod
    def from_packet(cls, packet: FeatureEvidencePacket) -> Self:
        return cls(
            schema_version="trace.feature-planning-projection.v1",
            feature_packet_id=packet.packet_id,
            feature_packet_sha256=contract_sha256(packet),
            lifecycle=packet.lifecycle,
            claim_ids=tuple(claim.claim_id for claim in packet.claims),
            allowed_claim_ids=packet.gate.allowed_claim_ids,
        )
