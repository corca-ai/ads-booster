"""Marketing decisions use coherent denominators, not popularity counts."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest
from pydantic import ValidationError

from ads_booster.agent.core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.bootstrap.integrations import AgentServiceIntegrationConfig, ConfiguredAgentTools
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.learning.funnel_analysis import FunnelAnalysisInput, analyze_funnels
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_integrations import UnusedResearchRunner

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.transport.json_types import JsonObject


def payload() -> JsonObject:
    return {
        "objective_stage": "d7",
        "source_note": "User-reported mature cohorts; not platform collection",
        "cohorts": [
            {
                "name": "A",
                "period": "week1",
                "audience": "workers",
                "currency": "USD",
                "spend": "300",
                "steps": [
                    {"name": "visit", "people": 1000},
                    {"name": "signup", "people": 300},
                    {"name": "activation", "people": 150},
                    {"name": "d7", "people": 15},
                ],
            },
            {
                "name": "B",
                "period": "week2",
                "audience": "students",
                "currency": "USD",
                "spend": "360",
                "steps": [
                    {"name": "visit", "people": 800},
                    {"name": "signup", "people": 160},
                    {"name": "activation", "people": 120},
                    {"name": "d7", "people": 36},
                ],
            },
        ],
    }


def test_objective_costs_and_stage_denominators_do_not_declare_a_winner() -> None:
    output = analyze_funnels(FunnelAnalysisInput.model_validate(payload()))
    cohorts = output["cohorts"]
    assert isinstance(cohorts, list)
    first, second = cohorts
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["objective_conversion_percent"] == "1.500000"
    assert second["objective_conversion_percent"] == "4.500000"
    assert first["cost_per_objective_person"] == "20.000000"
    assert second["cost_per_objective_person"] == "10.000000"
    assert first["stages"] == [
        {
            "from": "visit",
            "to": "signup",
            "numerator": 300,
            "denominator": 1000,
            "conversion_percent": "30.000000",
            "dropoff_people": 700,
        },
        {
            "from": "signup",
            "to": "activation",
            "numerator": 150,
            "denominator": 300,
            "conversion_percent": "50.000000",
            "dropoff_people": 150,
        },
        {
            "from": "activation",
            "to": "d7",
            "numerator": 15,
            "denominator": 150,
            "conversion_percent": "10.000000",
            "dropoff_people": 135,
        },
    ]
    limits = str(output["comparison_limits"])
    assert "Audiences differ" in limits
    assert "Observation periods differ" in limits
    assert "not causal lift" in limits
    assert "winner" not in output


@pytest.mark.parametrize("counts", [(10, 11), (0, 1), (-1, 0), (True, 0), (2.5, 1)])
def test_invalid_or_incoherent_people_counts_are_rejected(counts: tuple[object, object]) -> None:
    with pytest.raises(ValidationError):
        _ = FunnelAnalysisInput.model_validate(
            {
                "objective_stage": "goal",
                "source_note": "fixture",
                "cohorts": [
                    {
                        "name": "A",
                        "period": "week",
                        "audience": "workers",
                        "currency": "USD",
                        "steps": [
                            {"name": "entry", "people": counts[0]},
                            {"name": "goal", "people": counts[1]},
                        ],
                    }
                ],
            }
        )


def test_zero_denominator_missing_spend_and_mixed_currency_remain_explicit() -> None:
    request = FunnelAnalysisInput.model_validate(
        {
            "objective_stage": "goal",
            "source_note": "fixture",
            "cohorts": [
                {
                    "name": "A",
                    "period": "week",
                    "audience": "workers",
                    "currency": "USD",
                    "steps": [{"name": "entry", "people": 0}, {"name": "goal", "people": 0}],
                },
                {
                    "name": "B",
                    "period": "week",
                    "audience": "workers",
                    "currency": "JPY",
                    "spend": "100",
                    "steps": [{"name": "entry", "people": 10}, {"name": "goal", "people": 0}],
                },
            ],
        }
    )
    output = analyze_funnels(request)
    rows = output["cohorts"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    assert isinstance(rows[1], dict)
    assert rows[0]["objective_conversion_percent"] is None
    assert rows[0]["cost_per_objective_person"] is None
    assert rows[1]["cost_per_objective_person"] is None
    assert "Currencies differ" in str(output["comparison_limits"])


@pytest.mark.parametrize("spend", [True, 12.5, "-1", "NaN", "1e3", "0.0000001"])
def test_nonportable_or_ambiguous_spend_is_rejected(spend: object) -> None:
    with pytest.raises(ValidationError):
        _ = FunnelAnalysisInput.model_validate(
            {
                "objective_stage": "goal",
                "source_note": "fixture",
                "cohorts": [
                    {
                        "name": "A",
                        "period": "week",
                        "audience": "workers",
                        "currency": "USD",
                        "spend": spend,
                        "steps": [{"name": "entry", "people": 5}, {"name": "goal", "people": 3}],
                    }
                ],
            }
        )


class AnalyzeThenStop(AskThenStopReasoning):
    invalid_counts: bool = False
    no_financial_data: bool = False

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        data = payload()
        if self.no_financial_data:
            cohorts = data["cohorts"]
            assert isinstance(cohorts, list)
            for cohort in cohorts:
                assert isinstance(cohort, dict)
                _ = cohort.pop("currency")
                _ = cohort.pop("spend")
        if self.invalid_counts:
            cohorts = data["cohorts"]
            assert isinstance(cohorts, list)
            assert isinstance(cohorts[0], dict)
            steps = cohorts[0]["steps"]
            assert isinstance(steps, list)
            assert isinstance(steps[1], dict)
            steps[1]["people"] = 2000
        decision = (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="marketing.analyze",
                tool_input=data,
                expected_outcome="Compare retained-user acquisition",
                reasoning_summary="Calculate",
            )
            if len(self.requests) == 1
            else ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                expected_outcome="Report observed costs",
                reasoning_summary="A 20, B 10 USD; not causal",
            )
        )
        return _reasoning_result(request, decision)


@pytest.mark.parametrize("invalid_counts", [False, True])
@pytest.mark.parametrize("no_financial_data", [False, True])
def test_funnel_analysis_runs_through_installed_registration_and_canonical_receipt(
    tmp_path: Path,
    invalid_counts: bool,
    no_financial_data: bool,
) -> None:
    configured = ConfiguredAgentTools(AgentServiceIntegrationConfig(), UnusedResearchRunner())
    reasoning = AnalyzeThenStop()
    reasoning.invalid_counts = invalid_counts
    reasoning.no_financial_data = no_financial_data
    service = _service(tmp_path / "agent.db", reasoning)
    service.registry = ToolRegistry.from_registrations(configured.registrations(), now=NOW)
    service.tools = service.registry.adapters
    service.capability_policy = CapabilityPolicy(allowed_capability_ids=("marketing.analyze",))
    completed = service.create(_request(), now=NOW)
    assert completed.state is AgentRunState.COMPLETED
    receipts = [
        r
        for r in service.repository.records("trace", completed.run_id)
        if r.kind is AgentRecordKind.RECEIPT
    ]
    assert len(receipts) == 1
    assert receipts[0].payload["actual_cost_units"] == 0
    assert receipts[0].payload["disposition"] == ("failed" if invalid_counts else "no_effect")
    if invalid_counts:
        assert any(
            "funnel_counts_not_nested" in str(record.payload)
            for record in service.repository.records("trace", completed.run_id)
            if record.kind is AgentRecordKind.EVIDENCE
        )


@pytest.mark.parametrize("spend", [None, "0", "300"])
def test_currency_is_required_only_when_spend_is_reported(spend: str | None) -> None:
    data = payload()
    cohorts = data["cohorts"]
    assert isinstance(cohorts, list)
    for cohort in cohorts:
        assert isinstance(cohort, dict)
        _ = cohort.pop("currency")
        cohort["spend"] = spend
    if spend is not None:
        with pytest.raises(ValidationError, match="funnel_currency_required_for_spend"):
            _ = FunnelAnalysisInput.model_validate(data)
        return
    output = analyze_funnels(FunnelAnalysisInput.model_validate(data))
    rows = output["cohorts"]
    assert isinstance(rows, list)
    first = rows[0]
    assert isinstance(first, dict)
    assert first["currency"] is None
    assert first["spend"] is None
    assert first["cost_per_objective_person"] is None
    assert first["objective_conversion_percent"] == "1.500000"
    assert "Currencies differ" not in str(output["comparison_limits"])
