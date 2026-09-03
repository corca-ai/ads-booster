from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from ads_booster.contracts.marketing_agent import (
    ArtifactRequest,
    AttributionObservation,
    ClaimStatus,
    CreativeFormat,
    CreativeTreatment,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    ExperimentAllocationMethod,
    ExperimentEvaluation,
    ExperimentRegistration,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    LearningCandidate,
    MarketingHypothesis,
    MediaPlan,
    OutcomeDefinition,
    OutcomeScope,
    PortfolioRole,
    ProofKind,
    StrategyBrief,
    contract_sha256,
)

DIGEST = "a" * 64
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def test_cross_runtime_canonical_integer_fixture_has_a_frozen_digest() -> None:
    value: dict[str, object] = {
        "schema_version": "trace.experiment-evaluation.v1",
        "eligible_blocks": 2,
        "attribution_coverage_basis_points": 8000,
        "winner_hypothesis_id": None,
        "guardrail_failures": [],
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))

    assert sha256(canonical.encode()).hexdigest() == (
        "573f8dbbe8c45a2fb1ae1f2b34b0d557b56a88070b344947af0d6e7a15f713d2"
    )


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
        claim_ids=("claim-flow",),
        value_frame=f"value frame {hypothesis_id}",
        rationale="The product changes the lock screen throughout a user's day.",
        falsifier="The treatment does not improve the registered outcome.",
        proof_requirement="Show verified scenes assigned to different times.",
        conversation_motive="Ask which scene the viewer would schedule first.",
    )


def _experiment(
    *,
    outcome: OutcomeDefinition,
    allocation_method: ExperimentAllocationMethod = (
        ExperimentAllocationMethod.BALANCED_COMPLETE_BLOCKS
    ),
    causal_treatment_hypothesis_id: str | None = None,
    maximum_posts: int | None = None,
) -> ExperimentRegistration:
    return ExperimentRegistration(
        experiment_id="experiment-1",
        manipulated_component="value frame",
        held_constant_components=("account", "posting slot", "call to action"),
        activated_hypothesis_ids=("control", "challenger"),
        primary_outcome=outcome,
        allocation_method=allocation_method,
        causal_treatment_hypothesis_id=causal_treatment_hypothesis_id,
        diagnostic_metrics=("views", "replies"),
        guardrails=("unsupported claim", "broken deep link"),
        minimum_eligible_blocks=2,
        maximum_posts=(
            maximum_posts
            if maximum_posts is not None
            else (
                4
                if allocation_method
                is ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1
                else 8
            )
        ),
        maximum_duration_hours=24 * 14,
        minimum_attribution_coverage_basis_points=8_000,
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
                name="setup_completed",
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
            name="setup_completed",
            scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT,
            window_hours=72,
        )

    with pytest.raises(ValidationError, match="must not be presented as a causal estimate"):
        _ = OutcomeDefinition(
            name="setup_completed",
            scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
            window_hours=72,
            causal_estimand="difference in setup completion probability",
        )


def test_causal_estimation_requires_a_registered_server_allocation_and_treatment() -> None:
    outcome = OutcomeDefinition(
        name="setup_completed",
        scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT,
        window_hours=72,
        causal_estimand="difference in setup completion probability",
    )
    with pytest.raises(ValidationError, match="server-randomized complete blocks"):
        _ = _experiment(outcome=outcome)

    registered = _experiment(
        outcome=outcome,
        allocation_method=ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1,
        causal_treatment_hypothesis_id="challenger",
    )

    assert (
        registered.allocation_method
        is ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1
    )

    with pytest.raises(ValidationError, match="fixed complete block"):
        _ = _experiment(
            outcome=outcome,
            allocation_method=ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1,
            causal_treatment_hypothesis_id="challenger",
            maximum_posts=8,
        )


def test_contract_digest_is_independent_of_input_key_order() -> None:
    packet = _packet(
        claim_status=ClaimStatus.SOURCE_SUPPORTED,
        publication_allowed=False,
    )
    restored = FeatureEvidencePacket.model_validate_json(packet.model_dump_json())

    assert contract_sha256(packet) == contract_sha256(restored)


def _treatment(hypothesis_id: str) -> CreativeTreatment:
    return CreativeTreatment(
        treatment_id=f"treatment-{hypothesis_id}",
        hypothesis_id=hypothesis_id,
        format=CreativeFormat.NATIVE_SEQUENCE,
        hook=f"hook {hypothesis_id}",
        caption_direction="Explain one belief change without claiming installed behavior.",
        manipulated_component_value=f"value {hypothesis_id}",
        proof_narrative="Use a source-labeled explanatory sequence until runtime proof exists.",
        claim_ids=("claim-flow",),
        artifact_requests=(
            ArtifactRequest(
                request_id=f"artifact-{hypothesis_id}",
                capability_id="compose.explanation",
                proof_kind=ProofKind.COMPOSED_EXPLANATION,
                claim_ids=("claim-flow",),
                instructions="Compose a source-evidence-labeled explanation.",
            ),
        ),
    )


def test_media_plan_requires_one_treatment_per_distinct_hypothesis_and_human_review() -> None:
    plan = MediaPlan(
        schema_version="trace.media-plan.v1",
        plan_id="plan-1",
        campaign_id="campaign-1",
        account_id="trace_kr",
        experiment_id="experiment-1",
        strategy_brief_sha256=DIGEST,
        context_receipt_sha256="b" * 64,
        treatments=(_treatment("control"), _treatment("challenger")),
        publication_allowed=False,
        human_review_required=True,
        created_at=NOW,
    )
    assert len(plan.treatments) == 2

    duplicate_hypothesis = _treatment("control").model_copy(
        update={"treatment_id": "treatment-control-2"}
    )
    with pytest.raises(ValidationError, match="treatment_hypothesis_id"):
        _ = plan.model_copy(
            update={"treatments": (_treatment("control"), duplicate_hypothesis)}
        ).model_validate(
            plan.model_copy(
                update={"treatments": (_treatment("control"), duplicate_hypothesis)}
            ).model_dump()
        )


def test_artifact_request_cannot_escape_treatment_claims() -> None:
    base = _treatment("control")
    with pytest.raises(ValidationError, match="artifact requests may use only claims"):
        _ = CreativeTreatment(
            treatment_id=base.treatment_id,
            hypothesis_id=base.hypothesis_id,
            format=base.format,
            hook=base.hook,
            caption_direction=base.caption_direction,
            manipulated_component_value=base.manipulated_component_value,
            proof_narrative=base.proof_narrative,
            claim_ids=base.claim_ids,
            artifact_requests=(
                ArtifactRequest(
                    request_id="artifact-escape",
                    capability_id="capture.native_png",
                    proof_kind=ProofKind.INSTALLED_NATIVE_CAPTURE,
                    claim_ids=("claim-unbound",),
                    instructions="Capture the installed product.",
                ),
            ),
        )


def test_attribution_match_and_experiment_conclusion_fail_closed() -> None:
    with pytest.raises(ValidationError, match="matched attribution"):
        _ = AttributionObservation(
            schema_version="trace.attribution-observation.v1",
            observation_id="observation-1",
            campaign_id="campaign-1",
            experiment_id="experiment-1",
            assignment_id="assignment-1",
            publication_id="publication-1",
            scope="direct_response_attribution",
            window_hours=72,
            matched=True,
            observed_at=NOW,
        )

    with pytest.raises(ValidationError, match="cannot name a winner"):
        _ = ExperimentEvaluation(
            schema_version="trace.experiment-evaluation.v1",
            evaluation_id="evaluation-1",
            campaign_id="campaign-1",
            experiment_id="experiment-1",
            state="inconclusive",
            outcome_scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
            eligible_blocks=1,
            attribution_coverage_basis_points=3_000,
            winner_hypothesis_id="challenger",
            interpretation="Too little eligible evidence.",
            lineage_ids=("post-1",),
            evaluated_at=NOW,
        )


def test_learning_candidate_requires_independent_replication_lineages() -> None:
    with pytest.raises(ValidationError):
        _ = LearningCandidate(
            schema_version="trace.learning-candidate.v1",
            learning_id="learning-1",
            campaign_id="campaign-1",
            statement="Character-time framing may improve qualified conversation.",
            scope="KR iPhone source-supported feature campaigns",
            independent_lineage_ids=("experiment-1",),
            status="candidate",
            created_at=NOW,
        )
