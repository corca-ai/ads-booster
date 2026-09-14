from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

from pydantic import TypeAdapter

from ads_booster.agent.service.completion_evaluation import (
    CompletionEvaluationCase,
    CompletionEvaluationObservation,
    evaluate_completion_regressions,
)
from ads_booster.agent.service.task_completion import CompletionContext, TaskCompletionService
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_progress import ExactResponseCheck
from tests.marketing.agent_service.completion_fixtures import ExactTipAssessor, response_case

_CASES = TypeAdapter(tuple[CompletionEvaluationCase, ...])


def test_frozen_completion_regression_corpus_has_zero_policy_failures(tmp_path: Path) -> None:
    cases = _CASES.validate_python(
        json.loads(Path(__file__).with_name("completion_regression_corpus.json").read_text())
    )
    observations: list[CompletionEvaluationObservation] = []
    for index, case in enumerate(cases):
        fixture = response_case(tmp_path / f"{index}.db")
        assessor = ExactTipAssessor()
        task = fixture.task
        checkpoint = fixture.checkpoint
        if case.case_id.startswith("exact_response"):
            expected = (
                fixture.candidate.answer
                if case.case_id.endswith("without_model")
                else "Different exact answer"
            )
            obligation = task.obligations[0].model_copy(
                update={"verification": ExactResponseCheck(expected=expected)}
            )
            task = task.model_copy(update={"obligations": (obligation,)})
            checkpoint = checkpoint.model_copy(update={"spec_sha256": contract_sha256(task)})
            service = TaskCompletionService(fixture.repository, None)
        elif case.case_id == "semantic_verifier_unavailable_blocks":
            service = TaskCompletionService(fixture.repository, None)
        else:
            service = TaskCompletionService(fixture.repository, assessor)
        started = monotonic()
        result = service.assess(task, fixture.candidate, CompletionContext(fixture.run, checkpoint))
        observations.append(
            CompletionEvaluationObservation(
                case_id=case.case_id,
                disposition=result.disposition,
                actor_calls=0,
                assessor_calls=len(assessor.requests),
                effect_attempt_keys=(),
                elapsed_ms=max(0, int((monotonic() - started) * 1000)),
            )
        )

    report = evaluate_completion_regressions(cases, tuple(observations))

    assert report.false_completions == ()
    assert report.false_blocks == ()
    assert report.unnecessary_continuations == ()
    assert report.duplicate_effect_attempts == ()
    assert report.call_budget_violations == ()
    assert report.total_elapsed_ms >= 0


def test_expected_completion_that_waits_is_reported_as_false_block() -> None:
    case = CompletionEvaluationCase(
        case_id="unexpected-wait",
        oracle_kind="host_contract",
        expected_basis="The exact host contract is satisfied.",
        expected_disposition="satisfied",
        max_actor_calls=0,
        max_assessor_calls=0,
    )
    observation = CompletionEvaluationObservation(
        case_id=case.case_id,
        disposition="waiting",
        actor_calls=0,
        assessor_calls=0,
    )

    report = evaluate_completion_regressions((case,), (observation,))

    assert report.false_blocks == (case.case_id,)
