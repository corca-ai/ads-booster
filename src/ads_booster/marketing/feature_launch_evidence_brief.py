"""Immutable, planner-safe research provenance for a new Feature Launch session."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.marketing_agent import AgentIdentifier, contract_sha256
from ads_booster.contracts.models import ContractModel, Sha256Digest

type BriefScope = Literal["product_truth", "customer_intelligence", "market_evidence"]


class BriefEvidenceItem(ContractModel):
    """One receipt-grounded research observation, without source text or source location."""

    scope: BriefScope
    research_observation_id: AgentIdentifier
    research_observation_sha256: Sha256Digest
    receipt_sha256: Sha256Digest
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    decision_sha256: Sha256Digest
    source_sha256: Sha256Digest
    supported_allowed_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def require_unique_supported_claims(self) -> Self:
        if len(set(self.supported_allowed_claim_ids)) != len(self.supported_allowed_claim_ids):
            raise ValueError("brief evidence claim IDs must be unique")
        return self


class FeatureLaunchEvidenceBrief(ContractModel):
    """Completed research trace frozen as input provenance for exactly one launch task."""

    schema_version: Literal["trace.feature-launch-evidence-brief.v1"]
    brief_id: AgentIdentifier
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    research_goal_id: AgentIdentifier
    research_goal_sha256: Sha256Digest
    research_registry_snapshot_sha256: Sha256Digest
    research_session_id: AgentIdentifier
    research_trace_sha256: Sha256Digest
    research_evaluation_id: AgentIdentifier
    research_evaluation_sha256: Sha256Digest
    required_scopes: Annotated[tuple[BriefScope, ...], Field(min_length=1, max_length=3)]
    evidence: Annotated[tuple[BriefEvidenceItem, ...], Field(min_length=1, max_length=3)]
    created_at: datetime

    @model_validator(mode="after")
    def require_complete_scope_coverage(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != UTC.utcoffset(
            self.created_at
        ):
            raise ValueError("brief created_at must be UTC")
        if len(set(self.required_scopes)) != len(self.required_scopes):
            raise ValueError("brief required scopes must be unique")
        evidence_scopes = tuple(item.scope for item in self.evidence)
        if len(set(evidence_scopes)) != len(evidence_scopes):
            raise ValueError("brief evidence scopes must be unique")
        if set(evidence_scopes) != set(self.required_scopes):
            raise ValueError("brief evidence must cover exactly the required scopes")
        return self


class BriefEvidenceProjection(ContractModel):
    """Minimal evidence selection surface for the Feature Launch planner."""

    scope: BriefScope
    research_observation_id: AgentIdentifier
    research_observation_sha256: Sha256Digest
    supported_allowed_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)] = ()


class FeatureLaunchEvidenceBriefProjection(ContractModel):
    """Data-only planner projection; raw sources and research text cannot enter the prompt."""

    brief_id: AgentIdentifier
    brief_sha256: Sha256Digest
    required_scopes: Annotated[tuple[BriefScope, ...], Field(min_length=1, max_length=3)]
    evidence: Annotated[tuple[BriefEvidenceProjection, ...], Field(min_length=1, max_length=3)]

    @classmethod
    def from_brief(cls, brief: FeatureLaunchEvidenceBrief) -> Self:
        return cls(
            brief_id=brief.brief_id,
            brief_sha256=contract_sha256(brief),
            required_scopes=brief.required_scopes,
            evidence=tuple(
                BriefEvidenceProjection(
                    scope=item.scope,
                    research_observation_id=item.research_observation_id,
                    research_observation_sha256=item.research_observation_sha256,
                    supported_allowed_claim_ids=item.supported_allowed_claim_ids,
                )
                for item in brief.evidence
            ),
        )
