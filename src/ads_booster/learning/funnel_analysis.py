"""Descriptive funnel arithmetic over explicitly supplied, same-cohort counts."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue

Label = Annotated[str, Field(min_length=1, max_length=160)]


class FunnelStep(ContractModel):
    name: Label
    people: Annotated[int, Field(strict=True, ge=0, le=1_000_000_000_000)]


class FunnelCohort(ContractModel):
    name: Label
    period: Label
    audience: Label
    currency: (
        Annotated[
            str,
            Field(
                pattern=r"^[A-Z]{3}$",
                description="Omit if unknown; required when spend is supplied, including zero.",
            ),
        ]
        | None
    ) = None
    spend: (
        Annotated[
            str,
            Field(
                pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$",
                description="Nonnegative amount as a decimal string, e.g. 300.00; omit if unknown.",
            ),
        ]
        | None
    ) = None
    steps: Annotated[
        tuple[FunnelStep, ...],
        Field(
            min_length=2,
            max_length=8,
            description=(
                "Nested unique people from one mature cohort; not impressions or repeated events."
            ),
        ),
    ]

    @model_validator(mode="after")
    def require_nested_people(self) -> Self:
        if self.spend is not None and self.currency is None:
            msg = "funnel_currency_required_for_spend"
            raise PydanticCustomError(msg, "reported spend requires a currency")
        if len({step.name for step in self.steps}) != len(self.steps):
            msg = "funnel_duplicate_stage"
            raise PydanticCustomError(msg, "stage names must be unique")
        if any(right.people > left.people for left, right in pairwise(self.steps)):
            msg = "funnel_counts_not_nested"
            raise PydanticCustomError(
                msg,
                "ordered stages must count nested unique people from the same cohort",
            )
        return self


class FunnelAnalysisInput(ContractModel):
    """Counts are reported observations, never verified collection or effect authority."""

    objective_stage: Annotated[
        Label,
        Field(
            description=(
                "Exact steps[].name of an existing downstream stage shared by every cohort. "
                "Copy the stage name verbatim, not a rate description or a new metric name; "
                "the first (entry) stage cannot be the objective."
            ),
        ),
    ]
    cohorts: Annotated[tuple[FunnelCohort, ...], Field(min_length=1, max_length=8)]
    source_note: Annotated[str, Field(min_length=1, max_length=1000)]
    design: Literal["observational", "randomized_reported"] = "observational"

    @model_validator(mode="after")
    def require_comparable_definitions(self) -> Self:
        names = tuple(step.name for step in self.cohorts[0].steps)
        if self.objective_stage not in names[1:]:
            msg = "funnel_objective_missing"
            raise PydanticCustomError(msg, "objective must name a downstream stage")
        if any(tuple(step.name for step in cohort.steps) != names for cohort in self.cohorts):
            msg = "funnel_stage_mismatch"
            raise PydanticCustomError(msg, "cohorts must use the same ordered stage definitions")
        if len({cohort.name for cohort in self.cohorts}) != len(self.cohorts):
            msg = "funnel_duplicate_cohort"
            raise PydanticCustomError(msg, "cohort names must be unique")
        return self


def analyze_funnels(request: FunnelAnalysisInput) -> JsonObject:
    """Calculate explicit denominators, gaps and costs without choosing a causal winner."""
    limits: list[JsonValue] = [
        "Input is reported data, not independently collected or verified platform evidence.",
        (
            "Counts must be nested unique people with matching definitions and complete "
            "observation "
            "windows. Impressions, repeated events and immature retention cohorts do not qualify."
        ),
        (
            "Descriptive differences are not causal lift or statistical significance. "
            "No winner, sample-size target or spending approval is inferred."
        ),
    ]
    currencies = {cohort.currency for cohort in request.cohorts if cohort.currency is not None}
    if len(currencies) > 1:
        limits.append("Currencies differ: costs cannot be ranked or compared without conversion.")
    if len({cohort.period for cohort in request.cohorts}) > 1:
        limits.append("Observation periods differ; seasonality and cohort maturity may confound.")
    if len({cohort.audience for cohort in request.cohorts}) > 1:
        limits.append("Audiences differ; selection and acquisition mix may confound.")
    if request.design == "randomized_reported":
        randomization_limit = (
            "Randomization is reported, not verified. Check allocation, unit independence, "
            "tracking, stopping rules and guardrails before making an experimental decision."
        )
        limits.append(randomization_limit)
    return {
        "schema_version": "trace.marketing-funnel-analysis.v1",
        "objective_stage": request.objective_stage,
        "source_note": request.source_note,
        "design": request.design,
        "cohorts": [_cohort_result(cohort, request.objective_stage) for cohort in request.cohorts],
        "comparison_limits": limits,
        "next_decision": (
            "Choose a bottleneck tied to the objective; state one hypothesis, one changed "
            "variable, a control, the primary outcome, a retention/customer guardrail, "
            "measurement window and decision rule. Separate learning from budget allocation."
        ),
        "authority": "calculation_only_not_causal_evidence_or_approval",
    }


def _ratio(numerator: int | Decimal, denominator: int, *, percent: bool = False) -> str | None:
    if denominator == 0:
        return None
    return format(
        (Decimal(numerator) / denominator * (100 if percent else 1)).quantize(Decimal("0.000001")),
        "f",
    )


def _cohort_result(cohort: FunnelCohort, objective: str) -> JsonObject:
    entry = cohort.steps[0]
    outcome = next(step for step in cohort.steps if step.name == objective)
    stages: list[JsonValue] = []
    for previous, current in pairwise(cohort.steps):
        stages.append(
            {
                "from": previous.name,
                "to": current.name,
                "numerator": current.people,
                "denominator": previous.people,
                "conversion_percent": _ratio(current.people, previous.people, percent=True),
                "dropoff_people": previous.people - current.people,
            }
        )
    return {
        "name": cohort.name,
        "period": cohort.period,
        "audience": cohort.audience,
        "currency": cohort.currency,
        "spend": cohort.spend,
        "entry_stage": entry.name,
        "entry_people": entry.people,
        "objective_people": outcome.people,
        "objective_conversion_percent": _ratio(outcome.people, entry.people, percent=True),
        "cost_per_objective_person": (
            None if cohort.spend is None else _ratio(Decimal(cohort.spend), outcome.people)
        ),
        "stages": stages,
        "undefined_values": "null means a zero denominator or unreported spend; never zero cost.",
    }
