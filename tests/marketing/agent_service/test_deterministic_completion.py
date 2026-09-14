from __future__ import annotations

import json
from typing import Literal

import pytest

from ads_booster.agent.service.deterministic_completion import assess_deterministic_obligations
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_completion import CompletionCandidate
from ads_booster.contracts.task_progress import (
    ExactResponseCheck,
    RequiredEvidenceCheck,
    ResponseJsonFieldsCheck,
    ResponseLineCountCheck,
    TaskObligation,
)


@pytest.mark.parametrize(
    ("answer", "verification", "expected"),
    [
        ("exact", ExactResponseCheck(expected="exact"), True),
        ("first\nsecond", ResponseLineCountCheck(expected=2), True),
        (
            json.dumps({"name": "Trace", "status": "ready"}),
            ResponseJsonFieldsCheck(required_fields=("name", "status")),
            True,
        ),
        ("not json", ResponseJsonFieldsCheck(required_fields=("name",)), False),
        ("only one", ResponseLineCountCheck(expected=2), False),
    ],
)
def test_response_checks_are_exact_and_model_free(
    answer: str,
    verification: ExactResponseCheck | ResponseLineCountCheck | ResponseJsonFieldsCheck,
    expected: bool,
) -> None:
    candidate = _candidate(answer)
    result = assess_deterministic_obligations((_obligation(verification),), candidate)

    assert result.assessments[0].status == ("satisfied" if expected else "unsatisfied")
    assert result.semantic_obligations == ()


@pytest.mark.parametrize(("digest", "expected"), [("a" * 64, True), ("b" * 64, False)])
def test_required_evidence_check_binds_one_candidate_digest(digest: str, expected: bool) -> None:
    candidate = _candidate("artifact ready", evidence_sha256s=("a" * 64,))
    result = assess_deterministic_obligations(
        (_obligation(RequiredEvidenceCheck(evidence_sha256=digest), kind="artifact"),), candidate
    )

    assert result.assessments[0].status == ("satisfied" if expected else "unsatisfied")
    assert result.assessments[0].evidence_sha256s == ((digest,) if expected else ())


def _candidate(answer: str, *, evidence_sha256s: tuple[str, ...] = ()) -> CompletionCandidate:
    return CompletionCandidate(
        candidate_id="candidate",
        task_id="task",
        task_revision=1,
        answer=answer,
        answer_sha256=contract_sha256({"answer": answer}),
        evidence_sha256s=evidence_sha256s,
    )


def _obligation(
    verification: ExactResponseCheck
    | ResponseLineCountCheck
    | ResponseJsonFieldsCheck
    | RequiredEvidenceCheck,
    *,
    kind: Literal["response", "artifact", "effect"] = "response",
) -> TaskObligation:
    return TaskObligation(
        obligation_id="obligation",
        kind=kind,
        description="exact host condition",
        source_refs=("source",),
        verification=verification,
    )
