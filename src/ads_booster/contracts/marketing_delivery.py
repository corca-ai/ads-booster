"""Reviewable preparation contracts; existing channel owners retain execution facts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.contracts.creative_work import (
    CreativeScope,  # noqa: TC001 - Pydantic runtime schema
)
from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

Text = Annotated[str, Field(min_length=1, max_length=4000)]


class ReviewAsset(ContractModel):
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    sha256: Sha256Digest


class ProductionTarget(ContractModel):
    kind: Literal["production"] = "production"
    input_sha256: Sha256Digest
    source_assets: Annotated[tuple[ReviewAsset, ...], Field(max_length=16)] = ()
    instructions: Text
    preserve: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    max_cost_units: Annotated[int, Field(ge=0)]


class PublicationTarget(ContractModel):
    kind: Literal["publication"] = "publication"
    content_version: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=1, max_length=10000)]
    assets: Annotated[tuple[ReviewAsset, ...], Field(min_length=1, max_length=16)]
    account_id: Identifier
    channel: Literal["threads", "instagram", "x", "tiktok"]
    schedule_at: datetime | None = None
    conditions: Annotated[tuple[Text, ...], Field(min_length=1, max_length=16)]
    qa_summary: Text

    @model_validator(mode="after")
    def utc_schedule(self) -> Self:
        if self.schedule_at is not None and self.schedule_at.utcoffset() != UTC.utcoffset(None):
            message = "delivery_schedule_requires_utc"
            raise ValueError(message)
        return self


class ApprovalReference(ContractModel):
    proposal_id: Identifier
    target_sha256: Sha256Digest


class PaidBudgetTarget(ContractModel):
    kind: Literal["paid_budget"] = "paid_budget"
    account_id: Identifier
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    max_minor_units: Annotated[int, Field(gt=0)]
    purpose: Text


class PaidExecutionTarget(ContractModel):
    kind: Literal["paid_execution"] = "paid_execution"
    budget_approval: ApprovalReference
    account_id: Identifier
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    spend_minor_units: Annotated[int, Field(gt=0)]
    content_sha256: Sha256Digest
    audience: Text
    execution_conditions: Annotated[tuple[Text, ...], Field(min_length=1, max_length=16)]


class FormatTarget(ContractModel):
    kind: Literal["format_promotion", "format_deactivation"]
    format_id: Identifier
    version: Annotated[int, Field(ge=1)]
    evidence: Annotated[tuple[Text, ...], Field(min_length=1, max_length=16)]
    counterexamples: Annotated[tuple[Text, ...], Field(min_length=1, max_length=16)]
    applicability: Text
    recommendation: Text


class CodeImprovementTarget(ContractModel):
    kind: Literal["code_improvement"] = "code_improvement"
    problem: Text
    reproduction: Text
    impact: Text
    verification: Annotated[tuple[Text, ...], Field(min_length=1, max_length=16)]
    human_review_path: Text


class PostPublicationTarget(ContractModel):
    kind: Literal["post_publication_change"] = "post_publication_change"
    publication: ApprovalReference
    action: Literal["edit", "delete", "hide", "reply", "block", "report"]
    draft: Text
    impact: Text
    alternatives: Annotated[tuple[Text, ...], Field(min_length=1, max_length=3)]


ReviewTarget = Annotated[
    ProductionTarget
    | PublicationTarget
    | PaidBudgetTarget
    | PaidExecutionTarget
    | FormatTarget
    | CodeImprovementTarget
    | PostPublicationTarget,
    Field(discriminator="kind"),
]


class DeliveryProposal(ContractModel):
    schema_version: Literal["trace.delivery-proposal.v1"] = "trace.delivery-proposal.v1"
    proposal_id: Identifier
    scope: CreativeScope
    run_id: Identifier
    d1_campaign_id: Identifier | None = None
    rationale: Text
    target: ReviewTarget

    @property
    def target_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class DeliveryObservation(ContractModel):
    reference: Text
    evidence_sha256: Sha256Digest
    source: Literal["human_reported", "owner_readback"]


class DeliveryReviewPacket(ContractModel):
    proposal: DeliveryProposal
    revision: Annotated[int, Field(ge=1)]
    state: Literal["draft", "reviewed", "rejected", "scheduled_prepared", "cancelled"]
    approved_target_sha256: Sha256Digest | None = None
    reviewer_id: Identifier | None = None
    approval_expires_at: datetime | None = None
    observations: tuple[DeliveryObservation, ...] = ()
    external_execution_enabled: Literal[False] = False
    note: Literal["Prepared plan only; external execution stays with existing owners."] = (
        "Prepared plan only; external execution stays with existing owners."
    )
