"""Conservative evaluation and learning-candidate gates for marketing experiments."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import comb
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    CausalEffectEstimate,
    ExperimentEvaluation,
    ExperimentRegistration,
    LearningCandidate,
    OutcomeScope,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest

_MINIMUM_REPLICATIONS = 2
_CAUSAL_DECISION_THRESHOLD_BASIS_POINTS = 500
_CAUSAL_ARM_COUNT = 2


class AssignmentObservation(ContractModel):
    assignment_id: AgentIdentifier
    eligible_block_id: AgentIdentifier
    hypothesis_id: AgentIdentifier
    publication_id: AgentIdentifier | None = None
    product_event_id: AgentIdentifier | None = None
    eligible: bool
    attribution_observed: bool
    converted: bool | None = None
    guardrail_failures: Annotated[tuple[str, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def validate_attribution_observation(self) -> Self:
        if self.attribution_observed != (self.converted is not None):
            raise ValueError("observed attribution must state whether it converted")
        if self.product_event_id is not None and self.converted is not True:
            raise ValueError("a product event requires a converted observation")
        if self.attribution_observed and self.publication_id is None:
            raise ValueError("an observed attribution requires a publication")
        return self


class ExperimentEvaluationRequest(ContractModel):
    evaluation_id: AgentIdentifier
    campaign_id: AgentIdentifier
    registration: ExperimentRegistration
    observations: tuple[AssignmentObservation, ...]
    randomization_seed_sha256: Sha256Digest | None = None
    causal_exposure_verified: bool = False
    windows_complete: bool
    evaluated_at: AwareDatetime

    @model_validator(mode="after")
    def require_randomization_lineage_for_causal_outcomes(self) -> Self:
        causal = self.registration.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT
        if causal and self.randomization_seed_sha256 is None:
            raise ValueError("causal evaluation requires randomization seed lineage")
        if not causal and self.randomization_seed_sha256 is not None:
            raise ValueError("direct-response evaluation cannot carry randomization seed lineage")
        if not causal and self.causal_exposure_verified:
            raise ValueError("direct-response evaluation cannot carry causal exposure verification")
        return self


class LearningProposalRequest(ContractModel):
    learning_id: AgentIdentifier
    campaign_id: AgentIdentifier
    statement: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: Annotated[str, Field(min_length=1, max_length=500)]
    evaluations: tuple[ExperimentEvaluation, ...]
    created_at: AwareDatetime


@dataclass(frozen=True)
class _EvaluationFacts:
    active: set[str]
    complete_blocks: set[str]
    included: tuple[AssignmentObservation, ...]
    coverage_basis_points: int
    guardrail_failures: tuple[str, ...]


@dataclass(frozen=True)
class _Conclusion:
    state: Literal["evaluated", "inconclusive", "stopped"]
    winner_hypothesis_id: str | None
    interpretation: str
    causal_estimate: CausalEffectEstimate | None = None


def evaluate_experiment(request: ExperimentEvaluationRequest) -> ExperimentEvaluation:
    """Evaluate complete blocks, naming a causal winner only after an exact randomization test."""
    if len({item.assignment_id for item in request.observations}) != len(request.observations):
        raise ValueError("assignment observations must be unique")
    registration = request.registration
    active = set(registration.activated_hypothesis_ids)
    eligible = tuple(
        item for item in request.observations if item.eligible and item.hypothesis_id in active
    )
    guardrail_failures = tuple(
        sorted({failure for item in request.observations for failure in item.guardrail_failures})
    )
    complete_blocks = _complete_blocks(eligible, active)
    included = tuple(item for item in eligible if item.eligible_block_id in complete_blocks)
    coverage_basis_points = (
        round(10_000 * sum(item.attribution_observed for item in included) / len(included))
        if included
        else 0
    )
    lineage_ids = tuple(item.assignment_id for item in included) or (request.evaluation_id,)
    conclusion = _conclusion(
        request,
        _EvaluationFacts(
            active=active,
            complete_blocks=complete_blocks,
            included=included,
            coverage_basis_points=coverage_basis_points,
            guardrail_failures=guardrail_failures,
        ),
    )
    return ExperimentEvaluation(
        schema_version="trace.experiment-evaluation.v1",
        evaluation_id=request.evaluation_id,
        campaign_id=request.campaign_id,
        experiment_id=registration.experiment_id,
        state=conclusion.state,
        outcome_scope=registration.primary_outcome.scope,
        eligible_blocks=len(complete_blocks),
        attribution_coverage_basis_points=coverage_basis_points,
        winner_hypothesis_id=conclusion.winner_hypothesis_id,
        causal_estimate=conclusion.causal_estimate,
        interpretation=conclusion.interpretation,
        guardrail_failures=guardrail_failures,
        lineage_ids=lineage_ids,
        evaluated_at=request.evaluated_at,
    )


def _conclusion(
    request: ExperimentEvaluationRequest,
    facts: _EvaluationFacts,
) -> _Conclusion:
    prerequisite = _prerequisite_conclusion(request, facts)
    if prerequisite is not None:
        return prerequisite
    if request.registration.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT:
        return _causal_conclusion(request, facts)
    return _direct_response_conclusion(facts)


def _prerequisite_conclusion(
    request: ExperimentEvaluationRequest,
    facts: _EvaluationFacts,
) -> _Conclusion | None:
    registration = request.registration
    if facts.guardrail_failures:
        return _Conclusion(
            "stopped",
            None,
            "Guardrail failure stopped the experiment; no winner is named.",
        )
    if not request.windows_complete:
        return _Conclusion(
            "inconclusive",
            None,
            "Registered observation windows are incomplete.",
        )
    if len(facts.complete_blocks) < registration.minimum_eligible_blocks:
        return _Conclusion(
            "inconclusive",
            None,
            "The minimum number of complete eligible blocks was not reached.",
        )
    if facts.coverage_basis_points < registration.minimum_attribution_coverage_basis_points:
        return _Conclusion(
            "inconclusive",
            None,
            "Attribution coverage is below the pre-registered minimum.",
        )
    if any(
        not any(
            item.attribution_observed and item.hypothesis_id == hypothesis_id
            for item in facts.included
        )
        for hypothesis_id in facts.active
    ):
        return _Conclusion(
            "inconclusive",
            None,
            "At least one active hypothesis has no observed attribution.",
        )
    return None


def _causal_conclusion(
    request: ExperimentEvaluationRequest,
    facts: _EvaluationFacts,
) -> _Conclusion:
    if len(facts.complete_blocks) != request.registration.minimum_eligible_blocks:
        return _Conclusion(
            "inconclusive",
            None,
            "The pre-registered causal sample size was not completed exactly.",
        )
    if not request.causal_exposure_verified:
        return _Conclusion(
            "inconclusive",
            None,
            "Server-committed exposure slots have not been verified; no causal estimate is named.",
        )
    causal_estimate = _randomized_block_effect_estimate(
        facts.included,
        request.registration.causal_treatment_hypothesis_id,
        facts.active,
        request.randomization_seed_sha256,
    )
    effect = causal_estimate.treatment_minus_control_basis_points
    if (
        effect != 0
        and causal_estimate.two_sided_p_value_basis_points
        <= causal_estimate.decision_threshold_basis_points
    ):
        winner = (
            causal_estimate.treatment_hypothesis_id
            if effect > 0
            else causal_estimate.control_hypothesis_id
        )
        return _Conclusion(
            "evaluated",
            winner,
            (
                f"{winner} won the pre-registered randomized complete-block comparison "
                f"with a {effect} basis-point treatment-minus-control effect and an exact "
                "two-sided randomization test at or below the registered threshold."
            ),
            causal_estimate,
        )
    return _Conclusion(
        "inconclusive",
        None,
        (
            "The pre-registered randomized complete-block estimate did not clear "
            "the exact two-sided decision threshold; no causal winner is named."
        ),
        causal_estimate,
    )


def _direct_response_conclusion(facts: _EvaluationFacts) -> _Conclusion:
    rates = _descriptive_conversion_rates(facts.included, facts.active)
    top_rate = max(rates.values())
    winners = [hypothesis_id for hypothesis_id, rate in rates.items() if rate == top_rate]
    winner = winners[0] if len(winners) == 1 else None
    if winner is None:
        return _Conclusion(
            "inconclusive",
            None,
            "Direct-response attribution is tied across active hypotheses.",
        )
    return _Conclusion(
        "evaluated",
        winner,
        (
            f"{winner} has the highest observed direct-response attribution rate inside complete "
            "eligible blocks. This is descriptive attribution, not a causal effect."
        ),
    )


def propose_learning_candidate(request: LearningProposalRequest) -> LearningCandidate:
    """Require replicated, independently scoped evaluated lineages before proposing learning."""
    evaluations = request.evaluations
    if len(evaluations) < _MINIMUM_REPLICATIONS:
        raise ValueError("learning requires at least two experiment evaluations")
    if any(item.state != "evaluated" for item in evaluations):
        raise ValueError("learning requires evaluated rather than inconclusive evidence")
    if any(item.winner_hypothesis_id is None for item in evaluations):
        raise ValueError("learning requires a named descriptive winner in every evaluation")
    independent_campaigns = {item.campaign_id for item in evaluations}
    if len(independent_campaigns) != len(evaluations):
        raise ValueError("learning evaluations must come from independent campaigns")
    return LearningCandidate(
        schema_version="trace.learning-candidate.v1",
        learning_id=request.learning_id,
        campaign_id=request.campaign_id,
        statement=request.statement,
        scope=request.scope,
        independent_lineage_ids=tuple(item.evaluation_id for item in evaluations),
        status="candidate",
        created_at=request.created_at,
    )


def _complete_blocks(
    observations: tuple[AssignmentObservation, ...],
    active: set[str],
) -> set[str]:
    by_block: dict[str, list[str]] = defaultdict(list)
    for item in observations:
        by_block[item.eligible_block_id].append(item.hypothesis_id)
    return {
        block_id
        for block_id, hypotheses in by_block.items()
        if set(hypotheses) == active and len(hypotheses) == len(active)
    }


def _descriptive_conversion_rates(
    observations: tuple[AssignmentObservation, ...],
    active: set[str],
) -> dict[str, float]:
    totals = dict.fromkeys(active, 0)
    conversions = dict.fromkeys(active, 0)
    for item in observations:
        if not item.attribution_observed:
            continue
        if item.converted is None:
            raise ValueError("observed attribution must state whether it converted")
        totals[item.hypothesis_id] += 1
        conversions[item.hypothesis_id] += int(item.converted)
    if any(total == 0 for total in totals.values()):
        raise ValueError("every active hypothesis needs observed attribution")
    return {
        hypothesis_id: conversions[hypothesis_id] / totals[hypothesis_id]
        for hypothesis_id in active
    }


def _randomized_block_effect_estimate(
    observations: tuple[AssignmentObservation, ...],
    treatment_hypothesis_id: str | None,
    active: set[str],
    randomization_seed_sha256: str | None,
) -> CausalEffectEstimate:
    """Estimate a paired risk difference and exact two-sided randomization p-value.

    The server only enters this path for its immutable randomized-complete-block
    allocation method. A block contributes one observed binary outcome per arm.
    Under the sharp null, exchanging the two labels is equally likely, yielding a
    binomial sign-flip distribution over the non-zero paired differences.
    """
    if treatment_hypothesis_id is None or treatment_hypothesis_id not in active:
        raise ValueError("causal treatment must be an active hypothesis")
    if randomization_seed_sha256 is None:
        raise ValueError("causal evaluation requires randomization seed lineage")
    if len(active) != _CAUSAL_ARM_COUNT:
        raise ValueError("randomized block estimator requires exactly two active hypotheses")
    control_hypothesis_id = next(iter(active - {treatment_hypothesis_id}))
    outcomes_by_block: dict[str, dict[str, bool]] = defaultdict(dict)
    for item in observations:
        if not item.attribution_observed or item.converted is None:
            raise ValueError("every causal block needs observed attribution")
        outcomes_by_block[item.eligible_block_id][item.hypothesis_id] = item.converted
    differences = tuple(
        int(outcomes[treatment_hypothesis_id]) - int(outcomes[control_hypothesis_id])
        for _, outcomes in sorted(outcomes_by_block.items())
    )
    if not differences or any(set(outcomes) != active for outcomes in outcomes_by_block.values()):
        raise ValueError("randomized block estimator requires complete two-arm blocks")
    signed_sum = sum(differences)
    nonzero_pairs = sum(difference != 0 for difference in differences)
    p_value_basis_points = _exact_two_sided_sign_flip_p_value_basis_points(
        signed_sum,
        nonzero_pairs,
    )
    return CausalEffectEstimate(
        schema_version="trace.causal-effect-estimate.v1",
        estimator="randomized_complete_blocks_risk_difference.v1",
        control_hypothesis_id=control_hypothesis_id,
        treatment_hypothesis_id=treatment_hypothesis_id,
        randomization_seed_sha256=randomization_seed_sha256,
        treatment_minus_control_basis_points=round(10_000 * signed_sum / len(differences)),
        two_sided_p_value_basis_points=p_value_basis_points,
        decision_threshold_basis_points=_CAUSAL_DECISION_THRESHOLD_BASIS_POINTS,
    )


def _exact_two_sided_sign_flip_p_value_basis_points(
    signed_sum: int,
    nonzero_pairs: int,
) -> int:
    if nonzero_pairs == 0:
        return 10_000
    as_or_more_extreme: int = sum(
        comb(nonzero_pairs, positive_signs)
        for positive_signs in range(nonzero_pairs + 1)
        if abs(2 * positive_signs - nonzero_pairs) >= abs(signed_sum)
    )
    denominator: int = 1 << nonzero_pairs
    lower, remainder = divmod(10_000 * as_or_more_extreme, denominator)
    if remainder * 2 < denominator:
        return lower
    if remainder * 2 > denominator:
        return lower + 1
    return lower if lower % 2 == 0 else lower + 1
