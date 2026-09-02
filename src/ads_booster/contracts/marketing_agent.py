"""Versioned contracts for the Trace Threads marketing agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, LiteralString, Never, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.marketing_context import (  # noqa: TC001
    MarketingContextPlanningProjection,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest

if TYPE_CHECKING:
    from collections.abc import Iterable

AgentIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
CommitSha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


@unique
class FeatureLifecycle(StrEnum):
    SOURCE_CANDIDATE = "source_candidate"
    BUILD_CANDIDATE = "build_candidate"
    INSTALLED_CONFIRMED = "installed_confirmed"
    RELEASED = "released"
    RETRACTED = "retracted"


@unique
class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    SOURCE_SUPPORTED = "source_supported"
    BUILD_BOUND = "build_bound"
    INSTALLED_CONFIRMED = "installed_confirmed"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    RETRACTED = "retracted"


@unique
class EvidenceKind(StrEnum):
    SOURCE_BLOB = "source_blob"
    SOURCE_DIFF = "source_diff"
    TEST_DEFINITION = "test_definition"
    TEST_RUN = "test_run"
    SPECIFICATION = "specification"
    DOCUMENTATION = "documentation"
    COMMIT_CONTEXT = "commit_context"
    PULL_REQUEST_CONTEXT = "pull_request_context"
    BUILD_ATTESTATION = "build_attestation"
    INSTALL_RECEIPT = "install_receipt"
    RUNTIME_OBSERVATION = "runtime_observation"
    SCREENSHOT = "screenshot"
    VIDEO = "video"


@unique
class EvidenceResult(StrEnum):
    PASSED = "pass"
    FAILED = "fail"
    OBSERVED = "observed"
    ABSENT = "absent"
    INCONCLUSIVE = "inconclusive"


@unique
class PortfolioRole(StrEnum):
    CONTROL = "control"
    CHALLENGER = "challenger"


@unique
class OutcomeScope(StrEnum):
    DIRECT_RESPONSE_ATTRIBUTION = "direct_response_attribution"
    ESTIMATED_TREATMENT_EFFECT = "estimated_treatment_effect"


@unique
class ExperimentAllocationMethod(StrEnum):
    """How an experiment assigns its already-approved treatments to blocks."""

    BALANCED_COMPLETE_BLOCKS = "balanced_complete_blocks"
    SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1 = "server_randomized_complete_blocks_v1"


_CAUSAL_ARM_COUNT = 2


@unique
class CreativeFormat(StrEnum):
    NATIVE_SEQUENCE = "native_sequence"
    SCREEN_RECORDING = "screen_recording"
    EXPLANATORY_CAROUSEL = "explanatory_carousel"
    DESIGNED_STATIC = "designed_static"
    TEXT_ONLY = "text_only"


@unique
class ProofKind(StrEnum):
    INSTALLED_NATIVE_CAPTURE = "installed_native_capture"
    BOUND_SCREEN_RECORDING = "bound_screen_recording"
    COMPOSED_EXPLANATION = "composed_explanation"
    DESIGN_RENDER = "design_render"
    COPY_ONLY = "copy_only"


class EvidenceReference(ContractModel):
    evidence_id: AgentIdentifier
    kind: EvidenceKind
    source_uri: Annotated[str, Field(min_length=1, max_length=2000)]
    immutable_ref: Annotated[str, Field(min_length=1, max_length=500)]
    content_sha256: Sha256Digest
    result: EvidenceResult
    collected_at: datetime

    @model_validator(mode="after")
    def require_utc_collected_at(self) -> Self:
        _require_utc(self.collected_at, field="collected_at")
        return self


class FeatureClaim(ContractModel):
    claim_id: AgentIdentifier
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    status: ClaimStatus
    evidence_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=32)] = ()


class FeatureGate(ContractModel):
    publication_allowed: bool
    allowed_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)] = ()
    blocked_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)] = ()
    reasons: Annotated[tuple[str, ...], Field(max_length=32)] = ()


class FeatureEvidencePacket(ContractModel):
    schema_version: Literal["trace.feature-evidence.v1"]
    packet_id: AgentIdentifier
    feature_id: AgentIdentifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    lifecycle: FeatureLifecycle
    repository: Annotated[str, Field(min_length=1, max_length=300)]
    mutable_ref: Annotated[str, Field(min_length=1, max_length=300)]
    resolved_commit_sha: CommitSha
    tree_sha: CommitSha
    claims: Annotated[tuple[FeatureClaim, ...], Field(min_length=1, max_length=64)]
    evidence: Annotated[tuple[EvidenceReference, ...], Field(max_length=128)] = ()
    limitations: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    gate: FeatureGate
    observed_at: datetime

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> Self:
        _require_utc(self.observed_at, field="observed_at")
        claim_ids = _require_unique((claim.claim_id for claim in self.claims), field="claim_id")
        evidence_ids = _require_unique(
            (evidence.evidence_id for evidence in self.evidence),
            field="evidence_id",
        )
        for claim in self.claims:
            unknown_evidence = set(claim.evidence_ids) - evidence_ids
            if unknown_evidence:
                _raise_contract_error(
                    "unknown_claim_evidence",
                    "feature claims may reference only evidence in the same packet",
                )
        allowed = set(self.gate.allowed_claim_ids)
        blocked = set(self.gate.blocked_claim_ids)
        if allowed & blocked or not (allowed | blocked).issubset(claim_ids):
            _raise_contract_error(
                "invalid_feature_gate_claims",
                "feature gate claim IDs must be disjoint members of the packet",
            )
        statuses = {claim.claim_id: claim.status for claim in self.claims}
        if any(statuses[claim_id] != ClaimStatus.INSTALLED_CONFIRMED for claim_id in allowed):
            _raise_contract_error(
                "unverified_publishable_claim",
                "publication claims require installed-confirmed evidence",
            )
        _validate_installed_evidence(self, allowed)
        if self.gate.publication_allowed and not allowed:
            _raise_contract_error(
                "empty_publication_gate",
                "publication cannot be allowed without at least one allowed claim",
            )
        if self.gate.publication_allowed and self.lifecycle not in {
            FeatureLifecycle.INSTALLED_CONFIRMED,
            FeatureLifecycle.RELEASED,
        }:
            _raise_contract_error(
                "publication_lifecycle_not_installed",
                "publication requires an installed-confirmed or released feature lifecycle",
            )
        if not self.gate.publication_allowed and allowed:
            _raise_contract_error(
                "closed_publication_gate_has_claims",
                "a closed publication gate cannot expose allowed claims",
            )
        return self


class MarketingHypothesis(ContractModel):
    hypothesis_id: AgentIdentifier
    role: PortfolioRole
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]
    value_frame: Annotated[str, Field(min_length=1, max_length=1000)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    falsifier: Annotated[str, Field(min_length=1, max_length=1000)]
    proof_requirement: Annotated[str, Field(min_length=1, max_length=2000)]
    conversation_motive: Annotated[str, Field(min_length=1, max_length=1000)]
    reference_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()


class OutcomeDefinition(ContractModel):
    name: Literal[
        "first_open",
        "feature_start",
        "generation_completed",
        "scheduling_completed",
        "setup_completed",
    ]
    scope: OutcomeScope
    window_hours: Annotated[int, Field(ge=1, le=24 * 30)]
    causal_estimand: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def require_causal_estimand(self) -> Self:
        if self.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT and not self.causal_estimand:
            _raise_contract_error(
                "missing_causal_estimand",
                "estimated treatment effects require a named causal estimand",
            )
        if self.scope is OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION and self.causal_estimand:
            _raise_contract_error(
                "unexpected_causal_estimand",
                "direct-response attribution must not be presented as a causal estimate",
            )
        return self


class ExperimentRegistration(ContractModel):
    experiment_id: AgentIdentifier
    manipulated_component: Annotated[str, Field(min_length=1, max_length=500)]
    held_constant_components: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    allowed_incidental_differences: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    activated_hypothesis_ids: Annotated[
        tuple[AgentIdentifier, ...],
        Field(min_length=2, max_length=8),
    ]
    primary_outcome: OutcomeDefinition
    allocation_method: ExperimentAllocationMethod = (
        ExperimentAllocationMethod.BALANCED_COMPLETE_BLOCKS
    )
    causal_treatment_hypothesis_id: AgentIdentifier | None = None
    diagnostic_metrics: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()
    guardrails: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    minimum_eligible_blocks: Annotated[int, Field(ge=2, le=100)]
    maximum_posts: Annotated[int, Field(ge=2, le=1000)]
    maximum_duration_hours: Annotated[int, Field(ge=24, le=24 * 365)]
    minimum_attribution_coverage_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    stop_rules: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    inconclusive_when: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def require_registered_effect_estimator(self) -> Self:
        is_causal = self.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
        if is_causal:
            if (
                self.allocation_method
                is not ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1
            ):
                _raise_contract_error(
                    "missing_randomized_allocation",
                    "estimated treatment effects require server-randomized complete blocks",
                )
            if len(self.activated_hypothesis_ids) != _CAUSAL_ARM_COUNT:
                _raise_contract_error(
                    "invalid_causal_arm_count",
                    "the registered effect estimator compares exactly two hypotheses",
                )
            if self.maximum_posts != self.minimum_eligible_blocks * _CAUSAL_ARM_COUNT:
                _raise_contract_error(
                    "invalid_causal_sample_size",
                    "estimated treatment effects require one fixed complete block per post pair",
                )
            if self.causal_treatment_hypothesis_id not in self.activated_hypothesis_ids:
                _raise_contract_error(
                    "invalid_causal_treatment",
                    "estimated treatment effects require one active treatment hypothesis",
                )
        elif (
            self.allocation_method is not ExperimentAllocationMethod.BALANCED_COMPLETE_BLOCKS
            or self.causal_treatment_hypothesis_id is not None
        ):
            _raise_contract_error(
                "unexpected_causal_estimator",
                "direct-response attribution cannot register a causal estimator",
            )
        return self


class PositioningDecision(ContractModel):
    category: Annotated[str, Field(min_length=1, max_length=500)]
    current_alternative: Annotated[str, Field(min_length=1, max_length=1000)]
    differentiated_mechanism: Annotated[str, Field(min_length=1, max_length=1500)]
    proof_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]


class EvidenceDisposition(ContractModel):
    evidence_id: AgentIdentifier
    disposition: Literal["supports", "contradicts", "insufficient"]
    confidence_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    freshness: Literal["fresh", "stale", "unknown"]
    use: Literal["use_as_constraint", "test", "exclude"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class DecisionDossier(ContractModel):
    schema_version: Literal["trace.marketing-decision-dossier.v1"]
    situation: Literal[
        "new_launch",
        "experiment_result",
        "performance_regression",
        "market_event",
        "tool_failure",
    ]
    selected_icp_id: AgentIdentifier | Literal["research_needed"]
    selection_basis_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=32)] = ()
    positioning: PositioningDecision
    evidence_dispositions: Annotated[
        tuple[EvidenceDisposition, ...],
        Field(min_length=1, max_length=256),
    ]
    recommended_next_step: Literal[
        "research",
        "design_experiment",
        "hold_for_review",
        "reconcile_effect",
    ]
    reason: Annotated[str, Field(min_length=1, max_length=1500)]
    required_proof_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def validate_decision_semantics(self) -> Self:
        disposition_ids = tuple(item.evidence_id for item in self.evidence_dispositions)
        if len(set(disposition_ids)) != len(disposition_ids):
            _raise_contract_error(
                "duplicate_evidence_disposition",
                "decision dossier evidence dispositions must be unique",
            )
        if len(set(self.selection_basis_ids)) != len(self.selection_basis_ids):
            _raise_contract_error(
                "duplicate_selection_basis",
                "decision dossier selection basis IDs must be unique",
            )
        if len(set(self.required_proof_ids)) != len(self.required_proof_ids):
            _raise_contract_error(
                "duplicate_required_proof",
                "decision dossier required proof IDs must be unique",
            )
        if self.situation == "tool_failure":
            if self.recommended_next_step != "reconcile_effect":
                _raise_contract_error(
                    "unsafe_tool_failure_action",
                    "tool failures with unresolved effects require reconciliation",
                )
        elif self.recommended_next_step == "reconcile_effect":
            _raise_contract_error(
                "unexpected_effect_reconciliation",
                "effect reconciliation is reserved for tool failure situations",
            )
        if self.selected_icp_id == "research_needed" and self.recommended_next_step not in {
            "research",
            "hold_for_review",
        }:
            _raise_contract_error(
                "unsupported_icp_action",
                "an unresolved ICP cannot proceed directly to an experiment",
            )
        if any(
            item.freshness == "stale" and item.use != "exclude"
            for item in self.evidence_dispositions
        ):
            _raise_contract_error(
                "stale_evidence_used",
                "stale evidence must be excluded from the decision",
            )
        return self


class HypothesisReassessment(ContractModel):
    """One evidence-bound update to a hypothesis after observing an experiment."""

    hypothesis_id: AgentIdentifier
    disposition: Literal["retain", "revise", "retire"]
    rationale: Annotated[str, Field(min_length=1, max_length=1500)]
    next_test: Annotated[str | None, Field(max_length=1500)] = None


class MarketingReassessment(ContractModel):
    """A no-effect marketing decision produced from an immutable live evaluation."""

    schema_version: Literal["trace.marketing-reassessment.v1"]
    reassessment_id: AgentIdentifier
    campaign_id: AgentIdentifier
    trigger_evaluation_id: AgentIdentifier
    trigger_evaluation_sha256: Sha256Digest
    situation: Literal[
        "experiment_result",
        "performance_regression",
        "tool_failure",
    ]
    decision_dossier: DecisionDossier
    hypothesis_reassessments: Annotated[
        tuple[HypothesisReassessment, ...],
        Field(min_length=2, max_length=8),
    ]
    unanswered_questions: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    created_at: datetime

    @model_validator(mode="after")
    def validate_reassessment(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        if self.decision_dossier.situation != self.situation:
            _raise_contract_error(
                "reassessment_situation_mismatch",
                "the reassessment and its decision dossier must describe the same situation",
            )
        _ = _require_unique(
            (item.hypothesis_id for item in self.hypothesis_reassessments),
            field="reassessment_hypothesis_id",
        )
        return self


class StrategyBrief(ContractModel):
    schema_version: Literal["trace.strategy-brief.v1"]
    brief_id: AgentIdentifier
    campaign_id: AgentIdentifier
    account_id: AgentIdentifier
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    context_receipt_sha256: Sha256Digest
    business_outcome: Annotated[str, Field(min_length=1, max_length=1000)]
    audience_situation: Annotated[str, Field(min_length=1, max_length=2000)]
    belief_to_change: Annotated[str, Field(min_length=1, max_length=1000)]
    decision_dossier: DecisionDossier | None = None
    hypotheses: Annotated[tuple[MarketingHypothesis, ...], Field(min_length=2, max_length=8)]
    experiment: ExperimentRegistration
    created_at: datetime

    @model_validator(mode="after")
    def validate_portfolio(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        hypothesis_ids = _require_unique(
            (hypothesis.hypothesis_id for hypothesis in self.hypotheses),
            field="hypothesis_id",
        )
        controls = [
            hypothesis for hypothesis in self.hypotheses if hypothesis.role is PortfolioRole.CONTROL
        ]
        if len(controls) != 1:
            _raise_contract_error(
                "invalid_control_count",
                "a strategy portfolio requires exactly one control",
            )
        activated = set(self.experiment.activated_hypothesis_ids)
        if len(activated) != len(self.experiment.activated_hypothesis_ids):
            _raise_contract_error(
                "duplicate_activated_hypothesis",
                "activated hypothesis IDs must be unique",
            )
        if not activated <= hypothesis_ids:
            _raise_contract_error(
                "unknown_activated_hypothesis",
                "experiments may activate only hypotheses in the strategy portfolio",
            )
        if controls[0].hypothesis_id not in activated:
            _raise_contract_error(
                "inactive_control",
                "every experiment must activate the portfolio control",
            )
        if (
            self.experiment.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
            and self.experiment.causal_treatment_hypothesis_id == controls[0].hypothesis_id
        ):
            _raise_contract_error(
                "control_as_causal_treatment",
                "the portfolio control cannot be the causal treatment hypothesis",
            )
        return self


class ContextReceipt(ContractModel):
    schema_version: Literal["trace.context-receipt.v1"]
    receipt_id: AgentIdentifier
    campaign_id: AgentIdentifier
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    knowledge_snapshot_sha256: Sha256Digest
    capability_snapshot_sha256: Sha256Digest
    prompt_version: AgentIdentifier
    prompt_sha256: Sha256Digest
    output_schema_version: AgentIdentifier
    output_schema_sha256: Sha256Digest
    included_record_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=256)] = ()
    omitted_modules: Annotated[tuple[AgentIdentifier, ...], Field(max_length=64)] = ()
    marketing_context: MarketingContextPlanningProjection | None = None
    created_at: datetime

    @model_validator(mode="after")
    def require_utc_created_at(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        return self


class ArtifactRequest(ContractModel):
    request_id: AgentIdentifier
    capability_id: AgentIdentifier
    proof_kind: ProofKind
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()
    instructions: Annotated[str, Field(min_length=1, max_length=4000)]


class CreativeTreatment(ContractModel):
    treatment_id: AgentIdentifier
    hypothesis_id: AgentIdentifier
    format: CreativeFormat
    hook: Annotated[str, Field(min_length=1, max_length=1000)]
    caption_direction: Annotated[str, Field(min_length=1, max_length=2000)]
    manipulated_component_value: Annotated[str, Field(min_length=1, max_length=1000)]
    proof_narrative: Annotated[str, Field(min_length=1, max_length=2000)]
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]
    artifact_requests: Annotated[tuple[ArtifactRequest, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_artifact_requests(self) -> Self:
        request_ids = _require_unique(
            (request.request_id for request in self.artifact_requests),
            field="request_id",
        )
        if not request_ids:
            _raise_contract_error(
                "empty_artifact_request",
                "every creative treatment requires a proof request",
            )
        treatment_claims = set(self.claim_ids)
        for request in self.artifact_requests:
            if not set(request.claim_ids).issubset(treatment_claims):
                _raise_contract_error(
                    "artifact_claim_escape",
                    "artifact requests may use only claims in their creative treatment",
                )
        return self


class MediaPlan(ContractModel):
    schema_version: Literal["trace.media-plan.v1"]
    plan_id: AgentIdentifier
    campaign_id: AgentIdentifier
    account_id: AgentIdentifier
    experiment_id: AgentIdentifier
    strategy_brief_sha256: Sha256Digest
    context_receipt_sha256: Sha256Digest
    treatments: Annotated[tuple[CreativeTreatment, ...], Field(min_length=2, max_length=8)]
    publication_allowed: bool
    human_review_required: Literal[True]
    created_at: datetime

    @model_validator(mode="after")
    def validate_portfolio(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        _ = _require_unique(
            (treatment.treatment_id for treatment in self.treatments),
            field="treatment_id",
        )
        _ = _require_unique(
            (treatment.hypothesis_id for treatment in self.treatments),
            field="treatment_hypothesis_id",
        )
        return self


class ArtifactManifest(ContractModel):
    schema_version: Literal["trace.artifact-manifest.v1"]
    manifest_id: AgentIdentifier
    campaign_id: AgentIdentifier
    assignment_id: AgentIdentifier
    treatment_id: AgentIdentifier
    request_id: AgentIdentifier
    capability_id: AgentIdentifier
    artifact_uri: Annotated[str, Field(min_length=1, max_length=2000)]
    artifact_sha256: Sha256Digest
    input_sha256: Sha256Digest
    execution_id: AgentIdentifier | None = None
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()
    evidence_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=32)] = ()
    created_at: datetime

    @model_validator(mode="after")
    def require_utc_created_at(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        return self


class AttributionObservation(ContractModel):
    schema_version: Literal["trace.attribution-observation.v1"]
    observation_id: AgentIdentifier
    campaign_id: AgentIdentifier
    experiment_id: AgentIdentifier
    assignment_id: AgentIdentifier
    publication_id: AgentIdentifier
    product_event_id: AgentIdentifier | None = None
    scope: Literal["direct_response_attribution"]
    window_hours: Annotated[int, Field(ge=1, le=24 * 30)]
    matched: bool
    observed_at: datetime

    @model_validator(mode="after")
    def validate_match(self) -> Self:
        _require_utc(self.observed_at, field="observed_at")
        if self.matched != (self.product_event_id is not None):
            _raise_contract_error(
                "invalid_attribution_match",
                "matched attribution requires exactly one product event identity",
            )
        return self


class CausalEffectEstimate(ContractModel):
    """A pre-registered, randomized-block contrast rather than an attributed rate."""

    schema_version: Literal["trace.causal-effect-estimate.v1"]
    estimator: Literal["randomized_complete_blocks_risk_difference.v1"]
    control_hypothesis_id: AgentIdentifier
    treatment_hypothesis_id: AgentIdentifier
    randomization_seed_sha256: Sha256Digest
    treatment_minus_control_basis_points: Annotated[int, Field(ge=-10_000, le=10_000)]
    two_sided_p_value_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    decision_threshold_basis_points: Literal[500]

    @model_validator(mode="after")
    def require_distinct_arms(self) -> Self:
        if self.control_hypothesis_id == self.treatment_hypothesis_id:
            _raise_contract_error(
                "causal_estimate_same_arm",
                "a causal estimate needs distinct control and treatment hypotheses",
            )
        return self


class ExperimentEvaluation(ContractModel):
    schema_version: Literal["trace.experiment-evaluation.v1"]
    evaluation_id: AgentIdentifier
    campaign_id: AgentIdentifier
    experiment_id: AgentIdentifier
    state: Literal["evaluated", "inconclusive", "stopped"]
    outcome_scope: OutcomeScope
    eligible_blocks: Annotated[int, Field(ge=0)]
    attribution_coverage_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    winner_hypothesis_id: AgentIdentifier | None = None
    causal_estimate: CausalEffectEstimate | None = None
    interpretation: Annotated[str, Field(min_length=1, max_length=4000)]
    guardrail_failures: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    lineage_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=64)]
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_conclusion(self) -> Self:
        _require_utc(self.evaluated_at, field="evaluated_at")
        _ = _require_unique(self.lineage_ids, field="evaluation_lineage_id")
        if self.state != "evaluated" and self.winner_hypothesis_id is not None:
            _raise_contract_error(
                "winner_without_evaluation",
                "inconclusive or stopped experiments cannot name a winner",
            )
        if (
            self.outcome_scope is OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION
            and self.causal_estimate is not None
        ):
            _raise_contract_error(
                "causal_estimate_for_direct_outcome",
                "direct-response attribution cannot contain a causal estimate",
            )
        return self


class MarketingLearningApplicability(ContractModel):
    """Exact campaign selector that may receive a human-promoted learning."""

    schema_version: Literal["trace.marketing-learning-applicability.v1"]
    account_id: AgentIdentifier
    feature_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]
    mode: Literal["shadow", "assisted"]
    marketing_context_snapshot_sha256: Sha256Digest | None = None


class LearningCandidate(ContractModel):
    schema_version: Literal["trace.learning-candidate.v1"]
    learning_id: AgentIdentifier
    campaign_id: AgentIdentifier
    statement: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: Annotated[str, Field(min_length=1, max_length=500)]
    applicability: MarketingLearningApplicability | None = None
    independent_lineage_ids: Annotated[
        tuple[AgentIdentifier, ...],
        Field(min_length=2, max_length=32),
    ]
    status: Literal["candidate"]
    created_at: datetime

    @model_validator(mode="after")
    def require_independent_lineage(self) -> Self:
        _require_utc(self.created_at, field="created_at")
        _ = _require_unique(self.independent_lineage_ids, field="independent_lineage_id")
        return self


def contract_sha256(contract: ContractModel) -> str:
    """Return the canonical SHA-256 used for cross-runtime contract receipts."""
    encoded = json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()


def _validate_installed_evidence(packet: FeatureEvidencePacket, allowed: set[str]) -> None:
    evidence_by_id = {item.evidence_id: item for item in packet.evidence}
    for claim in packet.claims:
        if claim.claim_id not in allowed:
            continue
        installed_proof = any(
            evidence_by_id[evidence_id].kind
            in {EvidenceKind.INSTALL_RECEIPT, EvidenceKind.RUNTIME_OBSERVATION}
            and evidence_by_id[evidence_id].result
            in {EvidenceResult.PASSED, EvidenceResult.OBSERVED}
            for evidence_id in claim.evidence_ids
        )
        if not installed_proof:
            _raise_contract_error(
                "missing_installed_proof",
                "publication claims require a passing install or runtime observation",
            )


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        _raise_contract_error(
            "non_utc_datetime",
            "{field} must be UTC",
            context={"field": field},
        )


def _require_unique(values: Iterable[str], *, field: str) -> set[str]:
    materialized = tuple(values)
    unique = set(materialized)
    if len(unique) != len(materialized):
        _raise_contract_error(
            "duplicate_contract_identifier",
            "{field} values must be unique",
            context={"field": field},
        )
    return unique


def _raise_contract_error(
    error_type: LiteralString,
    error_message: LiteralString,
    *,
    context: dict[str, str] | None = None,
) -> Never:
    raise PydanticCustomError(error_type, error_message, context)
