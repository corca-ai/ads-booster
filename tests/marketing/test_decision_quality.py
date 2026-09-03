from __future__ import annotations

import pytest

from ads_booster.contracts.marketing_agent import DecisionDossier
from ads_booster.marketing.decision_quality import (
    DecisionQualityScenario,
    evaluate_decision_quality,
)


def _dossier(
    *,
    situation: str,
    icp: str,
    evidence: list[tuple[str, str, str, str]],
    next_step: str,
) -> DecisionDossier:
    return DecisionDossier.model_validate(
        {
            "schema_version": "trace.marketing-decision-dossier.v1",
            "situation": situation,
            "selected_icp_id": icp,
            "selection_basis_ids": [
                identifier
                for identifier, disposition, freshness, use in evidence
                if disposition == "supports" and freshness != "stale" and use != "exclude"
            ],
            "positioning": {
                "category": "dynamic lock-screen companion",
                "current_alternative": "one static wallpaper",
                "differentiated_mechanism": "scheduled character scenes change through the day",
                "proof_claim_ids": ["claim-installed"],
            },
            "evidence_dispositions": [
                {
                    "evidence_id": identifier,
                    "disposition": disposition,
                    "confidence_basis_points": 7000,
                    "freshness": freshness,
                    "use": use,
                    "reason": f"Bound disposition for {identifier}.",
                }
                for identifier, disposition, freshness, use in evidence
            ],
            "recommended_next_step": next_step,
            "reason": "Choose only the next bounded action supported by the evidence state.",
            "required_proof_ids": ["claim-installed"],
        }
    )


@pytest.mark.parametrize(
    ("scenario", "dossier"),
    [
        (
            {
                "scenario_id": "ambiguous-icp",
                "situation": "new_launch",
                "allowed_icp_ids": [],
                "required_evidence_ids": ["market-unknown"],
                "allowed_next_steps": ["research"],
                "require_research_needed": True,
            },
            _dossier(
                situation="new_launch",
                icp="research_needed",
                evidence=[("market-unknown", "insufficient", "unknown", "test")],
                next_step="research",
            ),
        ),
        (
            {
                "scenario_id": "verified-positioning",
                "situation": "new_launch",
                "allowed_icp_ids": ["ios-character-fans"],
                "required_evidence_ids": ["signal-fit", "runtime-proof"],
                "required_icp_basis_ids": ["signal-fit"],
                "allowed_next_steps": ["design_experiment"],
            },
            _dossier(
                situation="new_launch",
                icp="ios-character-fans",
                evidence=[
                    ("signal-fit", "supports", "fresh", "test"),
                    ("runtime-proof", "supports", "fresh", "use_as_constraint"),
                ],
                next_step="design_experiment",
            ),
        ),
        (
            {
                "scenario_id": "conflicting-sources",
                "situation": "new_launch",
                "allowed_icp_ids": ["ios-character-fans"],
                "required_evidence_ids": ["customer-support", "market-conflict"],
                "required_icp_basis_ids": ["customer-support"],
                "conflicting_evidence_ids": ["market-conflict"],
                "allowed_next_steps": ["design_experiment"],
            },
            _dossier(
                situation="new_launch",
                icp="ios-character-fans",
                evidence=[
                    ("customer-support", "supports", "fresh", "test"),
                    ("market-conflict", "contradicts", "fresh", "use_as_constraint"),
                ],
                next_step="design_experiment",
            ),
        ),
        (
            {
                "scenario_id": "performance-regression",
                "situation": "performance_regression",
                "allowed_icp_ids": ["ios-character-fans"],
                "required_evidence_ids": ["icp-fit", "complete-block-decline"],
                "required_icp_basis_ids": ["icp-fit"],
                "allowed_next_steps": ["hold_for_review"],
            },
            _dossier(
                situation="performance_regression",
                icp="ios-character-fans",
                evidence=[
                    ("icp-fit", "supports", "fresh", "use_as_constraint"),
                    ("complete-block-decline", "contradicts", "fresh", "use_as_constraint"),
                ],
                next_step="hold_for_review",
            ),
        ),
        (
            {
                "scenario_id": "fresh-event-stale-source",
                "situation": "market_event",
                "allowed_icp_ids": ["ios-character-fans"],
                "required_evidence_ids": ["fresh-event", "stale-roundup"],
                "stale_evidence_ids": ["stale-roundup"],
                "required_icp_basis_ids": ["fresh-event"],
                "allowed_next_steps": ["hold_for_review"],
            },
            _dossier(
                situation="market_event",
                icp="ios-character-fans",
                evidence=[
                    ("fresh-event", "supports", "fresh", "test"),
                    ("stale-roundup", "supports", "stale", "exclude"),
                ],
                next_step="hold_for_review",
            ),
        ),
        (
            {
                "scenario_id": "unknown-tool-effect",
                "situation": "tool_failure",
                "allowed_icp_ids": ["ios-character-fans"],
                "required_evidence_ids": ["icp-fit", "unknown-capture-receipt"],
                "required_icp_basis_ids": ["icp-fit"],
                "allowed_next_steps": ["reconcile_effect"],
            },
            _dossier(
                situation="tool_failure",
                icp="ios-character-fans",
                evidence=[
                    ("icp-fit", "supports", "fresh", "use_as_constraint"),
                    ("unknown-capture-receipt", "insufficient", "fresh", "use_as_constraint"),
                ],
                next_step="reconcile_effect",
            ),
        ),
    ],
)
def test_senior_marketer_decision_scenarios_pass_only_with_bound_reasoning(
    scenario: dict[str, object],
    dossier: DecisionDossier,
) -> None:
    contract = DecisionQualityScenario.model_validate(
        {
            "schema_version": "trace.marketing-decision-quality-scenario.v1",
            "supported_claim_ids": ["claim-installed"],
            "stale_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "require_research_needed": False,
            "required_icp_basis_ids": [],
            **scenario,
        }
    )

    baseline = evaluate_decision_quality(None, contract)
    improved = evaluate_decision_quality(dossier, contract)

    assert baseline.score_basis_points == 0
    assert baseline.gap_codes == ("missing_decision_dossier",)
    assert improved.passed is True
    assert improved.score_basis_points == 10_000


def test_decision_quality_rejects_empty_icp_basis_and_unbound_positioning() -> None:
    scenario = DecisionQualityScenario.model_validate(
        {
            "schema_version": "trace.marketing-decision-quality-scenario.v1",
            "scenario_id": "verified-positioning-mutants",
            "situation": "new_launch",
            "supported_claim_ids": ["claim-installed"],
            "allowed_icp_ids": ["ios-character-fans"],
            "required_icp_basis_ids": ["signal-fit"],
            "required_evidence_ids": ["signal-fit", "runtime-proof"],
            "allowed_next_steps": ["design_experiment"],
        }
    )
    dossier = _dossier(
        situation="new_launch",
        icp="ios-character-fans",
        evidence=[
            ("signal-fit", "supports", "fresh", "test"),
            ("runtime-proof", "supports", "fresh", "use_as_constraint"),
        ],
        next_step="design_experiment",
    )
    empty_basis = dossier.model_copy(update={"selection_basis_ids": ()})
    assert "weak_icp_basis" in evaluate_decision_quality(empty_basis, scenario).gap_codes

    raw = dossier.model_dump(mode="json")
    raw["positioning"]["proof_claim_ids"] = ["invented-claim"]
    unsupported_positioning = DecisionDossier.model_validate(raw)
    assert (
        "unsupported_positioning_claim"
        in evaluate_decision_quality(
            unsupported_positioning,
            scenario,
        ).gap_codes
    )


def test_decision_quality_rejects_hidden_conflict_and_unsafe_next_step() -> None:
    scenario = DecisionQualityScenario.model_validate(
        {
            "schema_version": "trace.marketing-decision-quality-scenario.v1",
            "scenario_id": "conflict-mutants",
            "situation": "new_launch",
            "supported_claim_ids": ["claim-installed"],
            "allowed_icp_ids": ["ios-character-fans"],
            "required_icp_basis_ids": ["customer-support"],
            "required_evidence_ids": ["customer-support", "market-conflict"],
            "conflicting_evidence_ids": ["market-conflict"],
            "allowed_next_steps": ["design_experiment"],
        }
    )
    hidden = _dossier(
        situation="new_launch",
        icp="ios-character-fans",
        evidence=[
            ("customer-support", "supports", "fresh", "test"),
            ("market-conflict", "supports", "fresh", "test"),
        ],
        next_step="hold_for_review",
    )
    evaluation = evaluate_decision_quality(hidden, scenario)
    assert "conflict_hidden" in evaluation.gap_codes
    assert "unsafe_next_step" in evaluation.gap_codes
