"""Deterministic grader for evidence-bound senior marketing decisions.

The grader does not decide strategy and has no tool authority. It verifies that a
``DecisionDossier`` makes the ICP, positioning evidence, conflicts, freshness, and
next action inspectable for one frozen scenario.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ads_booster.contracts.marketing_agent import DecisionDossier
from ads_booster.contracts.models import ContractModel, Identifier


class DecisionQualityScenario(ContractModel):
    schema_version: Literal["trace.marketing-decision-quality-scenario.v1"]
    scenario_id: Identifier
    situation: Literal[
        "new_launch",
        "experiment_result",
        "performance_regression",
        "market_event",
        "tool_failure",
    ]
    supported_claim_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    allowed_icp_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    required_icp_basis_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    required_evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=256)]
    conflicting_evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    stale_evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    allowed_next_steps: Annotated[
        tuple[
            Literal[
                "research",
                "design_experiment",
                "hold_for_review",
                "reconcile_effect",
            ],
            ...,
        ],
        Field(min_length=1, max_length=4),
    ]
    require_research_needed: bool = False


class DecisionQualityEvaluation(ContractModel):
    schema_version: Literal["trace.marketing-decision-quality-evaluation.v1"]
    scenario_id: Identifier
    passed: bool
    score_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    gap_codes: tuple[Identifier, ...]


def evaluate_decision_quality(
    dossier: DecisionDossier | None,
    scenario: DecisionQualityScenario,
) -> DecisionQualityEvaluation:
    """Grade only typed, independently checkable decision properties."""
    if dossier is None:
        return DecisionQualityEvaluation(
            schema_version="trace.marketing-decision-quality-evaluation.v1",
            scenario_id=scenario.scenario_id,
            passed=False,
            score_basis_points=0,
            gap_codes=("missing_decision_dossier",),
        )

    gaps = _decision_gap_codes(dossier, scenario)
    gaps.extend(_evidence_gap_codes(dossier, scenario))
    unique_gaps = tuple(dict.fromkeys(gaps))
    checks = 8
    score = round(10_000 * max(0, checks - len(unique_gaps)) / checks)
    return DecisionQualityEvaluation(
        schema_version="trace.marketing-decision-quality-evaluation.v1",
        scenario_id=scenario.scenario_id,
        passed=not unique_gaps,
        score_basis_points=score,
        gap_codes=unique_gaps,
    )


def _decision_gap_codes(
    dossier: DecisionDossier,
    scenario: DecisionQualityScenario,
) -> list[str]:
    gaps: list[str] = []
    if dossier.situation != scenario.situation:
        gaps.append("situation_mismatch")
    if not set(dossier.positioning.proof_claim_ids).issubset(scenario.supported_claim_ids):
        gaps.append("unsupported_positioning_claim")
    if scenario.require_research_needed:
        if dossier.selected_icp_id != "research_needed":
            gaps.append("unsupported_icp_selected")
    elif dossier.selected_icp_id not in scenario.allowed_icp_ids:
        gaps.append("unsupported_icp_selected")
    if dossier.recommended_next_step not in scenario.allowed_next_steps:
        gaps.append("unsafe_next_step")
    return gaps


def _evidence_gap_codes(
    dossier: DecisionDossier,
    scenario: DecisionQualityScenario,
) -> list[str]:
    gaps: list[str] = []
    dispositions = {item.evidence_id: item for item in dossier.evidence_dispositions}
    if set(dispositions) != set(scenario.required_evidence_ids):
        gaps.append("incomplete_evidence_disposition")
    if any(
        (item := dispositions.get(evidence_id)) is None
        or item.disposition != "contradicts"
        or item.use not in {"use_as_constraint", "test"}
        for evidence_id in scenario.conflicting_evidence_ids
    ):
        gaps.append("conflict_hidden")
    if any(
        (item := dispositions.get(evidence_id)) is None
        or item.freshness != "stale"
        or item.use != "exclude"
        for evidence_id in scenario.stale_evidence_ids
    ):
        gaps.append("stale_evidence_used")

    if dossier.selected_icp_id != "research_needed":
        required_basis = set(scenario.required_icp_basis_ids)
        if not required_basis or not required_basis.intersection(dossier.selection_basis_ids):
            gaps.append("weak_icp_basis")

    for evidence_id in dossier.selection_basis_ids:
        item = dispositions.get(evidence_id)
        if (
            item is None
            or item.freshness == "stale"
            or item.use == "exclude"
            or (item.disposition == "insufficient")
        ):
            gaps.append("weak_icp_basis")
            break
    return gaps
