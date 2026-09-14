"""Stable metrics for a frozen completion-regression corpus."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from ads_booster.contracts.models import ContractModel

type EvaluationDisposition = Literal["satisfied", "continue", "waiting", "blocked"]


class CompletionEvaluationCase(ContractModel):
    case_id: Annotated[str, Field(min_length=1, max_length=160)]
    oracle_kind: Literal["host_contract"]
    expected_basis: Annotated[str, Field(min_length=1, max_length=500)]
    expected_disposition: EvaluationDisposition
    max_actor_calls: Annotated[int, Field(ge=0)]
    max_assessor_calls: Annotated[int, Field(ge=0)]


class CompletionEvaluationObservation(ContractModel):
    case_id: Annotated[str, Field(min_length=1, max_length=160)]
    disposition: EvaluationDisposition
    actor_calls: Annotated[int, Field(ge=0)]
    assessor_calls: Annotated[int, Field(ge=0)]
    effect_attempt_keys: tuple[str, ...] = ()
    elapsed_ms: Annotated[int, Field(ge=0)] = 0


@dataclass(frozen=True, slots=True)
class CompletionEvaluationReport:
    false_completions: tuple[str, ...]
    false_blocks: tuple[str, ...]
    unnecessary_continuations: tuple[str, ...]
    duplicate_effect_attempts: tuple[str, ...]
    call_budget_violations: tuple[str, ...]
    actor_calls: int
    assessor_calls: int
    total_elapsed_ms: int


def evaluate_completion_regressions(
    cases: tuple[CompletionEvaluationCase, ...],
    observations: tuple[CompletionEvaluationObservation, ...],
) -> CompletionEvaluationReport:
    """Compare observed harness behavior with a version-controlled expectation set."""
    expected = {item.case_id: item for item in cases}
    observed = {item.case_id: item for item in observations}
    if len(expected) != len(cases) or len(observed) != len(observations):
        message = "completion evaluation case IDs must be unique"
        raise ValueError(message)
    if set(expected) != set(observed):
        message = "completion evaluation observations do not match frozen cases"
        raise ValueError(message)
    false_completions: list[str] = []
    false_blocks: list[str] = []
    unnecessary_continuations: list[str] = []
    duplicate_effect_attempts: list[str] = []
    call_budget_violations: list[str] = []
    for case_id, case in expected.items():
        observation = observed[case_id]
        if observation.disposition == "satisfied" and case.expected_disposition != "satisfied":
            false_completions.append(case_id)
        if (
            observation.disposition in {"waiting", "blocked"}
            and observation.disposition != case.expected_disposition
        ):
            false_blocks.append(case_id)
        if observation.disposition == "continue" and case.expected_disposition != "continue":
            unnecessary_continuations.append(case_id)
        duplicates = tuple(
            key for key, count in Counter(observation.effect_attempt_keys).items() if count > 1
        )
        duplicate_effect_attempts.extend(f"{case_id}:{key}" for key in duplicates)
        if (
            observation.actor_calls > case.max_actor_calls
            or observation.assessor_calls > case.max_assessor_calls
        ):
            call_budget_violations.append(case_id)
    return CompletionEvaluationReport(
        false_completions=tuple(false_completions),
        false_blocks=tuple(false_blocks),
        unnecessary_continuations=tuple(unnecessary_continuations),
        duplicate_effect_attempts=tuple(duplicate_effect_attempts),
        call_budget_violations=tuple(call_budget_violations),
        actor_calls=sum(item.actor_calls for item in observations),
        assessor_calls=sum(item.assessor_calls for item in observations),
        total_elapsed_ms=sum(item.elapsed_ms for item in observations),
    )
