"""Conservative evaluation and learning-candidate gates for marketing experiments."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    ExperimentEvaluation,
    ExperimentRegistration,
    LearningCandidate,
    OutcomeScope,
)
from ads_booster.contracts.models import ContractModel

_MINIMUM_REPLICATIONS = 2


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
    windows_complete: bool
    evaluated_at: AwareDatetime


class LearningProposalRequest(ContractModel):
    learning_id: AgentIdentifier
    campaign_id: AgentIdentifier
    statement: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: Annotated[str, Field(min_length=1, max_length=500)]
    evaluations: tuple[ExperimentEvaluation, ...]
    created_at: AwareDatetime


def evaluate_experiment(request: ExperimentEvaluationRequest) -> ExperimentEvaluation:
    """Evaluate only complete randomized blocks and label descriptive outcomes honestly."""
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
    state: Literal["evaluated", "inconclusive", "stopped"]
    winner: str | None
    if guardrail_failures:
        state = "stopped"
        interpretation = "Guardrail failure stopped the experiment; no winner is named."
        winner = None
    elif not request.windows_complete:
        state = "inconclusive"
        interpretation = "Registered observation windows are incomplete."
        winner = None
    elif len(complete_blocks) < registration.minimum_eligible_blocks:
        state = "inconclusive"
        interpretation = "The minimum number of complete eligible blocks was not reached."
        winner = None
    elif coverage_basis_points < registration.minimum_attribution_coverage_basis_points:
        state = "inconclusive"
        interpretation = "Attribution coverage is below the pre-registered minimum."
        winner = None
    elif any(
        not any(
            item.attribution_observed and item.hypothesis_id == hypothesis_id for item in included
        )
        for hypothesis_id in active
    ):
        state = "inconclusive"
        interpretation = "At least one active hypothesis has no observed attribution."
        winner = None
    elif registration.primary_outcome.scope is OutcomeScope.ESTIMATED_TREATMENT_EFFECT:
        state = "inconclusive"
        interpretation = "No eligible causal estimator is configured for this experiment."
        winner = None
    else:
        rates = _descriptive_conversion_rates(included, active)
        top_rate = max(rates.values())
        winners = [hypothesis_id for hypothesis_id, rate in rates.items() if rate == top_rate]
        winner = winners[0] if len(winners) == 1 else None
        state = "evaluated" if winner else "inconclusive"
        interpretation = (
            " ".join(
                (
                    f"{winner} has the highest observed direct-response attribution rate",
                    "inside complete eligible blocks. This is descriptive attribution,",
                    "not a causal effect.",
                )
            )
            if winner
            else "Direct-response attribution is tied across active hypotheses."
        )
    return ExperimentEvaluation(
        schema_version="trace.experiment-evaluation.v1",
        evaluation_id=request.evaluation_id,
        campaign_id=request.campaign_id,
        experiment_id=registration.experiment_id,
        state=state,
        outcome_scope=registration.primary_outcome.scope,
        eligible_blocks=len(complete_blocks),
        attribution_coverage_basis_points=coverage_basis_points,
        winner_hypothesis_id=winner,
        interpretation=interpretation,
        guardrail_failures=guardrail_failures,
        lineage_ids=lineage_ids,
        evaluated_at=request.evaluated_at,
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
