from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ads_booster.contracts.marketing_agent import (
    ExperimentAllocationMethod,
    ExperimentEvaluation,
    ExperimentRegistration,
    OutcomeDefinition,
    OutcomeScope,
)
from ads_booster.marketing.experiment_evaluation import (
    AssignmentObservation,
    ExperimentEvaluationRequest,
    LearningProposalRequest,
    evaluate_experiment,
    propose_learning_candidate,
)

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def registration(
    *,
    scope: OutcomeScope = OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
    minimum_blocks: int = 2,
    minimum_coverage_basis_points: int = 8_000,
) -> ExperimentRegistration:
    maximum_posts = 12
    if scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT:
        maximum_posts = minimum_blocks * 2
    return ExperimentRegistration(
        experiment_id="experiment-1",
        manipulated_component="value frame",
        held_constant_components=("account", "posting slot"),
        activated_hypothesis_ids=("control", "challenger"),
        primary_outcome=OutcomeDefinition(
            name="setup_completed",
            scope=scope,
            window_hours=72,
            causal_estimand=(
                "difference in setup completion probability"
                if scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
                else None
            ),
        ),
        allocation_method=(
            ExperimentAllocationMethod.SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1
            if scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
            else ExperimentAllocationMethod.BALANCED_COMPLETE_BLOCKS
        ),
        causal_treatment_hypothesis_id=(
            "challenger" if scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT else None
        ),
        guardrails=("product fidelity",),
        minimum_eligible_blocks=minimum_blocks,
        maximum_posts=maximum_posts,
        maximum_duration_hours=336,
        minimum_attribution_coverage_basis_points=minimum_coverage_basis_points,
        stop_rules=("guardrail failure",),
        inconclusive_when=("insufficient blocks", "low attribution coverage"),
    )


def observation(
    block: int,
    hypothesis: str,
    *,
    converted: bool | None,
    eligible: bool = True,
    guardrails: tuple[str, ...] = (),
) -> AssignmentObservation:
    return AssignmentObservation(
        assignment_id=f"assignment-{block}-{hypothesis}",
        eligible_block_id=f"block-{block}",
        hypothesis_id=hypothesis,
        eligible=eligible,
        attribution_observed=converted is not None,
        converted=converted,
        publication_id=(f"publication-{block}-{hypothesis}" if converted is not None else None),
        product_event_id=(f"event-{block}-{hypothesis}" if converted else None),
        guardrail_failures=guardrails,
    )


def complete_observations() -> tuple[AssignmentObservation, ...]:
    return (
        observation(1, "control", converted=False),
        observation(1, "challenger", converted=True),
        observation(2, "control", converted=False),
        observation(2, "challenger", converted=True),
    )


def causal_observations(blocks: int = 6) -> tuple[AssignmentObservation, ...]:
    return tuple(
        observation(block, hypothesis, converted=(hypothesis == "challenger"))
        for block in range(1, blocks + 1)
        for hypothesis in ("control", "challenger")
    )


def evaluate(
    observations: tuple[AssignmentObservation, ...],
    *,
    campaign_id: str = "campaign-1",
    experiment: ExperimentRegistration | None = None,
    windows_complete: bool = True,
    causal_exposure_verified: bool | None = None,
) -> ExperimentEvaluation:
    registered = experiment or registration()
    return evaluate_experiment(
        ExperimentEvaluationRequest(
            evaluation_id=f"evaluation-{campaign_id}",
            campaign_id=campaign_id,
            registration=registered,
            observations=observations,
            randomization_seed_sha256=(
                "d" * 64
                if registered.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
                else None
            ),
            causal_exposure_verified=(
                registered.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
                if causal_exposure_verified is None
                else causal_exposure_verified
            ),
            windows_complete=windows_complete,
            evaluated_at=NOW,
        )
    )


def test_complete_direct_response_blocks_name_a_descriptive_not_causal_winner() -> None:
    result = evaluate(complete_observations())

    assert result.state == "evaluated"
    assert result.winner_hypothesis_id == "challenger"
    assert "not a causal effect" in result.interpretation
    assert result.eligible_blocks == 2
    assert result.attribution_coverage_basis_points == 10_000


@pytest.mark.parametrize(
    ("observations", "windows_complete", "expected"),
    [
        (complete_observations()[:2], True, "minimum number"),
        (complete_observations(), False, "windows are incomplete"),
        (
            (
                observation(1, "control", converted=False),
                observation(1, "challenger", converted=None),
                observation(2, "control", converted=False),
                observation(2, "challenger", converted=True),
            ),
            True,
            "coverage",
        ),
    ],
)
def test_sparse_incomplete_or_low_coverage_results_are_inconclusive(
    observations: tuple[AssignmentObservation, ...],
    windows_complete: bool,
    expected: str,
) -> None:
    result = evaluate(observations, windows_complete=windows_complete)

    assert result.state == "inconclusive"
    assert result.winner_hypothesis_id is None
    assert expected in result.interpretation


def test_guardrail_failure_stops_before_winner_selection() -> None:
    observations = (
        *complete_observations()[:-1],
        observation(
            2,
            "challenger",
            converted=True,
            guardrails=("unsupported product claim",),
        ),
    )

    result = evaluate(observations)

    assert result.state == "stopped"
    assert result.winner_hypothesis_id is None
    assert result.guardrail_failures == ("unsupported product claim",)


def test_server_randomized_blocks_can_name_a_causal_winner_after_exact_test() -> None:
    result = evaluate(
        causal_observations(),
        experiment=registration(
            scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT,
            minimum_blocks=6,
        ),
    )

    assert result.state == "evaluated"
    assert result.winner_hypothesis_id == "challenger"
    assert result.causal_estimate is not None
    assert result.causal_estimate.treatment_minus_control_basis_points == 10_000
    assert result.causal_estimate.two_sided_p_value_basis_points == 312


def test_randomized_effect_with_insufficient_exact_evidence_remains_inconclusive() -> None:
    result = evaluate(
        complete_observations(),
        experiment=registration(scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT),
    )

    assert result.state == "inconclusive"
    assert result.winner_hypothesis_id is None
    assert result.causal_estimate is not None
    assert result.causal_estimate.two_sided_p_value_basis_points == 5_000


def test_causal_evaluation_fails_closed_without_server_verified_exposure_slots() -> None:
    result = evaluate(
        causal_observations(),
        experiment=registration(
            scope=OutcomeScope.ESTIMATED_TREATMENT_EFFECT,
            minimum_blocks=6,
        ),
        causal_exposure_verified=False,
    )

    assert result.state == "inconclusive"
    assert result.causal_estimate is None
    assert "exposure slots" in result.interpretation


def test_learning_candidate_requires_replication_across_independent_campaigns() -> None:
    first = evaluate(complete_observations(), campaign_id="campaign-1")
    second = evaluate(complete_observations(), campaign_id="campaign-2")
    candidate = propose_learning_candidate(
        LearningProposalRequest(
            learning_id="learning-1",
            campaign_id="campaign-2",
            statement="Character-time framing may improve qualified setup conversation.",
            scope="KR iPhone source-supported campaigns",
            evaluations=(first, second),
            created_at=NOW,
        )
    )

    assert candidate.status == "candidate"
    assert candidate.independent_lineage_ids == (
        "evaluation-campaign-1",
        "evaluation-campaign-2",
    )

    with pytest.raises(ValueError, match="independent campaigns"):
        _ = propose_learning_candidate(
            LearningProposalRequest(
                learning_id="learning-2",
                campaign_id="campaign-1",
                statement="Do not promote this.",
                scope="KR",
                evaluations=(first, first.model_copy(update={"evaluation_id": "evaluation-copy"})),
                created_at=NOW,
            )
        )
