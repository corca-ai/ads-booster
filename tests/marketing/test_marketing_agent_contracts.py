from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    ExperimentRegistration,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    MarketingHypothesis,
    OutcomeDefinition,
    OutcomeScope,
    PortfolioRole,
    StrategyBrief,
    contract_sha256,
)

DIGEST = "a" * 64
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id="source-flow",
        kind=EvidenceKind.SOURCE_BLOB,
        source_uri="https://github.com/corca-ai/Trace_iOS/blob/abc/flow.swift",
        immutable_ref="abc:flow.swift",
        content_sha256=DIGEST,
        result=EvidenceResult.OBSERVED,
        collected_at=NOW,
    )


def _packet(*, claim_status: ClaimStatus, publication_allowed: bool) -> FeatureEvidencePacket:
    allowed = ("claim-flow",) if publication_allowed else ()
    blocked = () if publication_allowed else ("claim-flow",)
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id="packet-lockscreen-v1",
        feature_id="trace.lockscreen.ai-concepts",
        title="AI lock screen concepts",
        lifecycle=FeatureLifecycle.SOURCE_CANDIDATE,
        repository="corca-ai/Trace_iOS",
        mutable_ref="refs/heads/develop",
        resolved_commit_sha="b" * 40,
        tree_sha="c" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-flow",
                text="The flow creates scheduled scenes from a character image.",
                status=claim_status,
                evidence_ids=("source-flow",),
            ),
        ),
        evidence=(_evidence(),),
        gate=FeatureGate(
            publication_allowed=publication_allowed,
            allowed_claim_ids=allowed,
            blocked_claim_ids=blocked,
            reasons=() if publication_allowed else ("fresh install evidence is missing",),
        ),
        observed_at=NOW,
    )


def _hypothesis(hypothesis_id: str, role: PortfolioRole) -> MarketingHypothesis:
    return MarketingHypothesis(
        hypothesis_id=hypothesis_id,
        role=role,
        value_frame=f"value frame {hypothesis_id}",
        rationale="The product changes the lock screen throughout a user's day.",
        falsifier="The treatment does not improve the registered outcome.",
        proof_requirement="Show verified scenes assigned to different times.",
        conversation_motive="Ask which scene the viewer would schedule first.",
    )


def _experiment(*, outcome: OutcomeDefinition) -> ExperimentRegistration:
    return ExperimentRegistration(
        experiment_id="experiment-1",
        manipulated_component="value frame",
        held_constant_components=("account", "posting slot", "call to action"),
        activated_hypothesis_ids=("control", "challenger"),
        primary_outcome=outcome,
        diagnostic_metrics=("views", "replies"),
        guardrails=("unsupported claim", "broken deep link"),
        minimum_eligible_blocks=2,
        maximum_posts=8,
        maximum_duration_hours=24 * 14,
        minimum_attribution_coverage=0.8,
        stop_rules=("stop on a product-fidelity violation",),
        inconclusive_when=("minimum eligible blocks are not reached",),
    )


def _brief(*, hypotheses: tuple[MarketingHypothesis, ...]) -> StrategyBrief:
    return StrategyBrief(
        schema_version="trace.strategy-brief.v1",
        brief_id="brief-1",
        campaign_id="campaign-1",
        account_id="trace_kr",
        feature_packet_id="packet-lockscreen-v1",
        feature_packet_sha256=DIGEST,
        context_receipt_sha256="b" * 64,
        business_outcome="Increase completed AI lock-screen setups.",
        audience_situation="An iPhone user wants a favorite character to accompany their day.",
        belief_to_change="A lock screen can be a changing character story, not a static image.",
        hypotheses=hypotheses,
        experiment=_experiment(
            outcome=OutcomeDefinition(
                name="attributed_setup_completion",
                scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
                window_hours=72,
            ),
        ),
        created_at=NOW,
    )


def test_source_supported_claim_cannot_open_the_publication_gate() -> None:
    with pytest.raises(ValidationError, match="installed-confirmed evidence"):
        _ = _packet(claim_status=ClaimStatus.SOURCE_SUPPORTED, publication_allowed=True)


def test_source_candidate_packet_can_support_shadow_strategy_with_closed_gate() -> None:
    packet = _packet(
        claim_status=ClaimStatus.SOURCE_SUPPORTED,
        publication_allowed=False,
    )

    assert packet.lifecycle is FeatureLifecycle.SOURCE_CANDIDATE
    assert packet.gate.allowed_claim_ids == ()
    assert packet.gate.blocked_claim_ids == ("claim-flow",)


def test_strategy_requires_exactly_one_active_control() -> None:
    with pytest.raises(ValidationError, match="exactly one control"):
        _ = _brief(
            hypotheses=(
                _hypothesis("control", PortfolioRole.CHALLENGER),
                _hypothesis("challenger", PortfolioRole.CHALLENGER),
            ),
        )

    brief = _brief(
        hypotheses=(
            _hypothesis("control", PortfolioRole.CONTROL),
            _hypothesis("challenger", PortfolioRole.CHALLENGER),
        ),
    )
    assert brief.experiment.activated_hypothesis_ids == ("control", "challenger")


def test_direct_response_and_causal_outcomes_cannot_be_conflated() -> None:
    with pytest.raises(ValidationError, match="causal estimand"):
        _ = OutcomeDefinition(
            name="setup_effect",
            scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT,
            window_hours=72,
        )

    with pytest.raises(ValidationError, match="must not be presented as a causal estimate"):
        _ = OutcomeDefinition(
            name="attributed_setup",
            scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
            window_hours=72,
            causal_estimand="difference in setup completion probability",
        )


def test_contract_digest_is_independent_of_input_key_order() -> None:
    packet = _packet(
        claim_status=ClaimStatus.SOURCE_SUPPORTED,
        publication_allowed=False,
    )
    restored = FeatureEvidencePacket.model_validate_json(packet.model_dump_json())

    assert contract_sha256(packet) == contract_sha256(restored)
