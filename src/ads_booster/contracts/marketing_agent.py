"""Versioned contracts for the Trace Threads marketing agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, LiteralString, Never, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

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
        if self.gate.publication_allowed and not allowed:
            _raise_contract_error(
                "empty_publication_gate",
                "publication cannot be allowed without at least one allowed claim",
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
    value_frame: Annotated[str, Field(min_length=1, max_length=1000)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    falsifier: Annotated[str, Field(min_length=1, max_length=1000)]
    proof_requirement: Annotated[str, Field(min_length=1, max_length=2000)]
    conversation_motive: Annotated[str, Field(min_length=1, max_length=1000)]
    reference_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()


class OutcomeDefinition(ContractModel):
    name: AgentIdentifier
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
    diagnostic_metrics: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()
    guardrails: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    minimum_eligible_blocks: Annotated[int, Field(ge=2, le=100)]
    maximum_posts: Annotated[int, Field(ge=2, le=1000)]
    maximum_duration_hours: Annotated[int, Field(ge=24, le=24 * 365)]
    minimum_attribution_coverage: Annotated[float, Field(ge=0, le=1)]
    stop_rules: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    inconclusive_when: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]


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
    created_at: datetime

    @model_validator(mode="after")
    def require_utc_created_at(self) -> Self:
        _require_utc(self.created_at, field="created_at")
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
