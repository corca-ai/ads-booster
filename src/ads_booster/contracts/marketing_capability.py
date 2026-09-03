"""Portable contracts for host-owned, observe-only marketing capabilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.transport.json_types import JsonObject  # noqa: TC001 - Pydantic resolves it.

ResearchCapabilityScope = Literal[
    "product_truth",
    "customer_intelligence",
    "market_evidence",
]
ResearchActionIdentifier = Literal[
    "observe.product_truth",
    "observe.customer_intelligence",
    "observe.market_evidence",
]
FeatureLaunchIntentIdentifier = Literal[
    "stop",
    "request_more_evidence",
    "propose_shadow_strategy",
]


class ResearchCapabilityConfigurationBounds(ContractModel):
    claim_ids_max: Literal[16]
    question_max_chars: Literal[1000]
    counter_evidence_question_max_chars: Literal[1000]


class ResearchCapabilityManifest(ContractModel):
    action_id: ResearchActionIdentifier
    scope: ResearchCapabilityScope
    capability_id: ResearchActionIdentifier
    owner_id: Literal["trace-marketing.dynamic-evidence-research"]
    effect_class: Literal["observe"]
    request_schema_sha256: Sha256Digest
    worst_case_cost_units: Annotated[int, Field(ge=1, le=24)]
    approval_policy: Literal["none"]
    configuration_bounds: ResearchCapabilityConfigurationBounds

    @model_validator(mode="after")
    def require_action_binding(self) -> Self:
        expected = f"observe.{self.scope}"
        if self.action_id != expected or self.capability_id != expected:
            message = "research capability action does not match its scope"
            raise ValueError(message)
        return self


class ResearchCapabilitySnapshot(ContractModel):
    schema_version: Literal["trace.research-capability-snapshot.v1"]
    skill_id: Literal["evidence_research.v1"]
    skill_sha256: Sha256Digest
    planner_protocol_sha256: Sha256Digest
    capabilities: Annotated[
        tuple[ResearchCapabilityManifest, ...],
        Field(min_length=1, max_length=3),
    ]

    @model_validator(mode="after")
    def require_unique_capabilities(self) -> Self:
        actions = tuple(item.action_id for item in self.capabilities)
        scopes = tuple(item.scope for item in self.capabilities)
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        if (
            len(set(actions)) != len(actions)
            or len(set(scopes)) != len(scopes)
            or len(set(capability_ids)) != len(capability_ids)
        ):
            message = "research capability snapshot entries must be unique"
            raise ValueError(message)
        return self


class FeatureLaunchIntentOption(ContractModel):
    intent_id: FeatureLaunchIntentIdentifier
    version: Literal["trace.feature-launch-intent.v1"]
    owner_id: Literal["trace-marketing.hosted-feature-launch-run"]
    effect_class: Literal["none"]
    input_schema_sha256: Literal["217b305284a2eeffc4c15aa244e79dd6da6fce1a7138d656a9f27c7d5477f6fc"]
    output_schema_sha256: Literal[
        "38cf82491b68ac5d14a64a6c5e83733f5a9df58b0e4b50fbac2efab161a1a8a2"
    ]
    eligibility: Literal[
        "always",
        "insufficient_evidence_present",
        "exact_research_continuation_present",
    ]
    precondition: Literal[
        "none",
        "needs_input_terminal_projection",
        "research_continuation_required",
    ]
    fixed_cost_units: Literal[0]
    approval_policy: Literal["none"]
    requested_scopes: Annotated[tuple[ResearchCapabilityScope, ...], Field(max_length=3)] = ()

    @model_validator(mode="after")
    def require_intent_shape(self) -> Self:
        if len(set(self.requested_scopes)) != len(self.requested_scopes):
            message = "feature launch intent scopes must be unique"
            raise ValueError(message)
        expected_policy = {
            "stop": ("always", "none"),
            "request_more_evidence": (
                "insufficient_evidence_present",
                "needs_input_terminal_projection",
            ),
            "propose_shadow_strategy": (
                "exact_research_continuation_present",
                "research_continuation_required",
            ),
        }[self.intent_id]
        if (self.eligibility, self.precondition) != expected_policy:
            message = "feature launch intent option is inconsistent"
            raise ValueError(message)
        if self.intent_id != "request_more_evidence" and self.requested_scopes:
            message = "only request-more intent may expose requested scopes"
            raise ValueError(message)
        return self


class FeatureLaunchIntentSnapshot(ContractModel):
    schema_version: Literal["trace.feature-launch-intent-snapshot.v1"]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    research_result_sha256: Sha256Digest
    intents: Annotated[tuple[FeatureLaunchIntentOption, ...], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def require_canonical_intent_order(self) -> Self:
        full_order: tuple[FeatureLaunchIntentIdentifier, ...] = (
            "stop",
            "request_more_evidence",
            "propose_shadow_strategy",
        )
        actual = tuple(item.intent_id for item in self.intents)
        expected = tuple(item for item in full_order if item in actual)
        if not actual or actual[0] != "stop" or actual != expected:
            message = "feature launch intent snapshot order is invalid"
            raise ValueError(message)
        return self


class FeatureLaunchIntentPlannerReceipt(ContractModel):
    schema_version: Literal["trace.planner-invocation-receipt.v1"]
    provider_id: Literal["official-codex-cli"]
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    prompt_sha256: Sha256Digest
    context_sha256: Sha256Digest
    output_schema_sha256: Literal[
        "38cf82491b68ac5d14a64a6c5e83733f5a9df58b0e4b50fbac2efab161a1a8a2"
    ]
    planner_protocol_sha256: Literal[
        "64890efb66606cc77e5facacaf4c7f62ee1cad18f60247548a1eda98f5566826"
    ]


class FeatureLaunchNextIntentDecision(ContractModel):
    schema_version: Literal["trace.feature-launch-next-intent-decision.v1"]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    research_result_sha256: Sha256Digest
    intent_snapshot_sha256: Sha256Digest
    intent_id: FeatureLaunchIntentIdentifier
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    requested_scope: ResearchCapabilityScope | None
    planner_receipt: FeatureLaunchIntentPlannerReceipt

    @model_validator(mode="after")
    def require_requested_scope_shape(self) -> Self:
        if (self.intent_id == "request_more_evidence") != (self.requested_scope is not None):
            message = "next intent requested scope is inconsistent"
            raise ValueError(message)
        return self


class ResearchToolCallProof(ContractModel):
    schema_version: Literal["trace.tool-call.v1"]
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=512)]
    capability_id: ResearchActionIdentifier
    descriptor_sha256: Sha256Digest
    request_schema_sha256: Sha256Digest
    input_sha256: Sha256Digest
    effect_class: Literal["observe"]

    @property
    def digest(self) -> str:
        return _contract_sha256(self)


class ResearchBoundInvocationProof(ContractModel):
    schema_version: Literal["trace.bound-tool-invocation.v1"]
    call: ResearchToolCallProof
    request: JsonObject

    @model_validator(mode="after")
    def require_input_binding(self) -> Self:
        expected = _json_sha256(
            {
                "schema_version": self.schema_version,
                "request_schema_sha256": self.call.request_schema_sha256,
                "request": self.request,
            }
        )
        if self.call.input_sha256 != expected:
            message = "research invocation input digest mismatch"
            raise ValueError(message)
        return self


class ResearchToolReceiptProof(ContractModel):
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    call_sha256: Sha256Digest
    approval_grant_sha256: None
    disposition: Literal["succeeded"]
    actual_cost_units: Annotated[int, Field(ge=0, le=24)]
    receipt_sha256: Sha256Digest


class ResearchObservationProof(ContractModel):
    schema_version: Literal["trace.evidence-research-observation.v2"]
    observation_id: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    ]
    scope: ResearchCapabilityScope
    receipt_sha256: Sha256Digest
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    feature_packet_sha256: Sha256Digest
    decision_sha256: Sha256Digest
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    evidence_summary: Annotated[str, Field(min_length=1, max_length=2000)]
    caveats: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    trust_state: Literal[
        "packet_bound",
        "caller_supplied_projection",
        "verified_source_receipts",
        "unverified_model_proposal",
    ]
    supported_claim_ids: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    evidence_status: Literal["sufficient", "insufficient"]
    observed_at: datetime

    @model_validator(mode="after")
    def require_safe_observation(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(None):
            message = "research observation proof time must be UTC"
            raise ValueError(message)
        if len(set(self.supported_claim_ids)) != len(self.supported_claim_ids):
            message = "research observation proof claim IDs must be unique"
            raise ValueError(message)
        if self.trust_state == "unverified_model_proposal" and self.evidence_status == "sufficient":
            message = "unverified research observation proof cannot be sufficient"
            raise ValueError(message)
        return self


class ResearchHandResultProof(ContractModel):
    schema_version: Literal["trace.dynamic-research-hand-result-proof.v1"]
    goal_id: Annotated[str, Field(min_length=1, max_length=128)]
    call_id: Annotated[str, Field(min_length=1, max_length=512)]
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    feature_packet_sha256: Sha256Digest
    decision_sha256: Sha256Digest
    disposition: Literal["succeeded", "failed"]
    actual_cost_units: Annotated[int, Field(ge=0, le=24)]
    iteration: Annotated[int, Field(ge=1, le=3)]
    scope: ResearchCapabilityScope
    evidence_status: Literal["sufficient", "insufficient"] | None
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    source_artifact_sha256: Sha256Digest | None = None
    trust_state: Literal[
        "packet_bound",
        "caller_supplied_projection",
        "verified_source_receipts",
        "unverified_model_proposal",
    ]
    supported_claim_ids: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    caveats: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    observed_at: datetime

    @model_validator(mode="after")
    def require_safe_projection(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(None):
            message = "research hand proof time must be UTC"
            raise ValueError(message)
        if len(set(self.supported_claim_ids)) != len(self.supported_claim_ids):
            message = "research hand proof claim IDs must be unique"
            raise ValueError(message)
        if (self.disposition == "succeeded") != (self.evidence_status is not None):
            message = "research hand proof disposition does not match evidence availability"
            raise ValueError(message)
        return self

    @property
    def digest(self) -> str:
        return _contract_sha256(self)


class ResearchProofChainEntry(ContractModel):
    sequence: Annotated[int, Field(ge=1, le=3)]
    iteration: Annotated[int, Field(ge=1, le=3)]
    action_id: ResearchActionIdentifier
    scope: ResearchCapabilityScope
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    receipt_sha256: Sha256Digest
    observation_sha256: Sha256Digest
    actual_cost_units: Annotated[int, Field(ge=0, le=24)]
    invocation: ResearchBoundInvocationProof
    receipt: ResearchToolReceiptProof
    observation: ResearchObservationProof
    hand_result: ResearchHandResultProof

    @model_validator(mode="after")
    def require_canonical_lineage(self) -> Self:
        call = self.invocation.call
        request = self.invocation.request
        decision = request.get("decision")
        goal = request.get("goal")
        if not isinstance(decision, dict) or not isinstance(goal, dict):
            message = "research proof request is missing its decision or goal"
            raise ValueError(message)  # noqa: TRY004 - Pydantic wraps validation errors.
        expected_action = f"observe.{self.scope}"
        relationships_match = (
            self.action_id == expected_action
            and call.capability_id == self.action_id
            and self.call_sha256 == call.digest
            and self.request_sha256 == call.input_sha256
            and self.receipt.call_id == call.call_id
            and self.receipt.call_sha256 == self.call_sha256
            and self.receipt.actual_cost_units == self.hand_result.actual_cost_units
            and self.actual_cost_units == self.receipt.actual_cost_units
            and self.receipt.receipt_sha256 == self.hand_result.digest
            and self.receipt_sha256 == self.receipt.receipt_sha256
            and self.observation.receipt_sha256 == self.receipt_sha256
            and self.observation.call_sha256 == self.call_sha256
            and self.observation.request_sha256 == self.request_sha256
            and self.observation_sha256 == _contract_sha256(self.observation)
            and self.observation.feature_packet_sha256 == self.hand_result.feature_packet_sha256
            and self.observation.decision_sha256 == self.hand_result.decision_sha256
            and self.observation.scope == self.scope
            and self.observation.source_ref == self.hand_result.source_ref
            and self.observation.source_sha256 == self.hand_result.source_sha256
            and self.observation.evidence_summary == self.hand_result.summary
            and self.observation.caveats == self.hand_result.caveats
            and self.observation.trust_state == self.hand_result.trust_state
            and self.observation.supported_claim_ids == self.hand_result.supported_claim_ids
            and self.observation.evidence_status == self.hand_result.evidence_status
            and self.observation.observed_at == self.hand_result.observed_at
            and self.hand_result.call_id == call.call_id
            and self.hand_result.call_sha256 == call.digest
            and self.hand_result.request_sha256 == call.input_sha256
            and self.hand_result.iteration == self.iteration
            and self.hand_result.scope == self.scope
            and decision.get("iteration") == self.iteration
            and decision.get("scope") == self.scope
            and decision.get("action_id") == self.action_id
            and self.hand_result.decision_sha256 == _json_sha256(decision)
            and self.hand_result.goal_id == goal.get("goal_id")
        )
        if not relationships_match:
            message = "research proof envelope lineage mismatch"
            raise ValueError(message)
        return self


def _contract_sha256(contract: ContractModel) -> str:
    return _json_sha256(contract.model_dump(mode="json"))


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return sha256(encoded).hexdigest()


__all__ = [
    "FeatureLaunchIntentIdentifier",
    "FeatureLaunchIntentOption",
    "FeatureLaunchIntentPlannerReceipt",
    "FeatureLaunchIntentSnapshot",
    "FeatureLaunchNextIntentDecision",
    "ResearchActionIdentifier",
    "ResearchBoundInvocationProof",
    "ResearchCapabilityConfigurationBounds",
    "ResearchCapabilityManifest",
    "ResearchCapabilityScope",
    "ResearchCapabilitySnapshot",
    "ResearchHandResultProof",
    "ResearchObservationProof",
    "ResearchProofChainEntry",
    "ResearchToolCallProof",
    "ResearchToolReceiptProof",
]
