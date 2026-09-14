# pyright: reportUnnecessaryComparison=false
"""Host-owned completion checks that do not require semantic judgment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, assert_never

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.task_completion import ObligationAssessment
from ads_booster.contracts.task_progress import (
    ExactResponseCheck,
    RequiredEvidenceCheck,
    ResponseJsonFieldsCheck,
    ResponseLineCountCheck,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)

if TYPE_CHECKING:
    from ads_booster.contracts.task_completion import CompletionCandidate
    from ads_booster.contracts.task_progress import TaskObligation


@dataclass(frozen=True, slots=True)
class DeterministicCompletionResult:
    assessments: tuple[ObligationAssessment, ...]
    semantic_obligations: tuple[TaskObligation, ...]
    uncovered_requirements: tuple[str, ...]


def assess_deterministic_obligations(
    obligations: tuple[TaskObligation, ...], candidate: CompletionCandidate
) -> DeterministicCompletionResult:
    """Evaluate only checks selected by trusted task admission."""
    assessments: list[ObligationAssessment] = []
    semantic: list[TaskObligation] = []
    uncovered: list[str] = []
    for obligation in obligations:
        check = obligation.verification
        if check is None:
            semantic.append(obligation)
            continue
        matched, mechanism, evidence_sha256s = _evaluate(check, candidate)
        assessments.append(
            ObligationAssessment(
                obligation_id=obligation.obligation_id,
                status="satisfied" if matched else "unsatisfied",
                mechanism=mechanism,
                reason=(
                    f"Host deterministic check satisfied: {obligation.description}"
                    if matched
                    else f"Host deterministic check failed: {obligation.description}"
                ),
                evidence_sha256s=evidence_sha256s,
            )
        )
        if obligation.required and not matched:
            uncovered.append(obligation.description)
    return DeterministicCompletionResult(tuple(assessments), tuple(semantic), tuple(uncovered))


def _evaluate(
    check: ExactResponseCheck
    | ResponseLineCountCheck
    | ResponseJsonFieldsCheck
    | RequiredEvidenceCheck,
    candidate: CompletionCandidate,
) -> tuple[bool, str, tuple[str, ...]]:
    match check:
        case ExactResponseCheck():
            return candidate.answer == check.expected, "host_exact_response", ()
        case ResponseLineCountCheck():
            return (
                len(candidate.answer.splitlines()) == check.expected,
                "host_response_line_count",
                (),
            )
        case ResponseJsonFieldsCheck():
            try:
                payload = _JSON_OBJECT.validate_json(candidate.answer)
            except ValidationError:
                return False, "host_response_json_fields", ()
            return set(check.required_fields).issubset(payload), "host_response_json_fields", ()
        case RequiredEvidenceCheck():
            matched = check.evidence_sha256 in candidate.evidence_sha256s
            return (
                matched,
                "host_required_evidence",
                (check.evidence_sha256,) if matched else (),
            )
        case unreachable:
            assert_never(unreachable)
