"""Regression tests for the offline, versioned Marketing OS scorecard."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.evidence_research_operator import (
    ResearchObservation,
    ResearchScope,
    ResearchState,
    ResearchStepEvaluation,
)
from ads_booster.marketing.feature_launch_operator import (
    FeatureLaunchEvaluation,
    FeatureLaunchObservation,
)
from ads_booster.marketing.marketing_os_scorecard import (
    MarketingOsEvalCase,
    MarketingOsEvalExpectation,
    MarketingOsEvalInput,
    MarketingOsEvalObservation,
    MarketingOsRunnerMetadata,
    MarketingOsScorecard,
    MarketingOsScorecardError,
    MarketingOsScorecardThreshold,
    MarketingOsTraceEvent,
    compare_marketing_os_scorecards,
)
from ads_booster.marketing.runtime import canonical_json_object
from tests.marketing.marketing_os_scorecard_runner import (
    FixtureEnvironment,
    FixtureReceiptAuthority,
    FixtureScenario,
    TestOnlyMarketingOsRunner,
    TestOnlyMarketingOsTraceVerifier,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "marketing_os_scorecard" / "v1"


def _load_cases() -> tuple[MarketingOsEvalCase, ...]:
    inputs = cast("list[dict[str, object]]", _load_fixture("inputs.json")["cases"])
    expectations = cast(
        "list[dict[str, object]]", _load_fixture("grader_expectations.json")["cases"]
    )
    expected_by_id = {
        cast("str", expectation["case_id"]): MarketingOsEvalExpectation.model_validate(expectation)
        for expectation in expectations
    }
    return tuple(
        MarketingOsEvalCase(
            MarketingOsEvalInput.model_validate(input_case),
            expected_by_id[cast("str", input_case["case_id"])],
        )
        for input_case in inputs
    )


def _load_fixture(name: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8")))


def _metadata(*, runner_id: str = "test-only-runner") -> MarketingOsRunnerMetadata:
    return MarketingOsRunnerMetadata(
        schema_version="trace.marketing-os-scorecard-runner.v1",
        runner_id=runner_id,
        runner_sha256="a" * 64,
        provider_id="test-only",
        model_id="deterministic-fixtures.v1",
        prompt_sha256="b" * 64,
        skill_registry_sha256="c" * 64,
        trial=1,
    )


def _scorecard(root: Path, environment: FixtureEnvironment) -> MarketingOsScorecard:
    return MarketingOsScorecard(
        TestOnlyMarketingOsTraceVerifier(root / "verifier", environment.receipt_authority)
    )


def _environment() -> FixtureEnvironment:
    raw_cases = cast(
        "dict[str, dict[str, object]]", _load_fixture("tool_environment.json")["cases"]
    )
    return FixtureEnvironment(
        scenarios={
            case_id: FixtureScenario(
                customer_status=cast(
                    "Literal['sufficient', 'insufficient']",
                    scenario.get("customer_status", "sufficient"),
                ),
                counter_evidence_found=cast("bool", scenario.get("counter_evidence_found", False)),
                blocked_claim=cast("bool", scenario.get("blocked_claim", False)),
                mismatched_brief=cast("bool", scenario.get("mismatched_brief", False)),
            )
            for case_id, scenario in raw_cases.items()
        },
        receipt_authority=FixtureReceiptAuthority(),
    )


def test_versioned_marketing_os_scorecard_grades_real_multiskill_paths(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()
    runner = TestOnlyMarketingOsRunner(tmp_path, environment)

    report = _scorecard(tmp_path, environment).evaluate(cases, runner, _metadata())

    assert len(cases) == 5
    assert report.process_pass_count == 5
    assert report.environment_pass_count == 5
    assert report.pass_count == 5
    assert [result.case_id for result in report.results] == [
        "trace.marketing-os.v2.case-001",
        "trace.marketing-os.v2.case-002",
        "trace.marketing-os.v2.case-003",
        "trace.marketing-os.v2.case-004",
        "trace.marketing-os.v2.case-005",
    ]
    assert all(result.passed for result in report.results)
    assert all("expected" not in item.model_dump_json() for item in runner.seen_inputs)
    assert all("scenario" not in item.model_dump_json() for item in runner.seen_inputs)

    by_id = {result.case_id: result.assessment for result in report.results}
    assert by_id["trace.marketing-os.v2.case-001"].launch_state == "completed"
    assert by_id["trace.marketing-os.v2.case-002"].launch_state == "not_started"
    assert by_id["trace.marketing-os.v2.case-003"].launch_outcome_passed is False
    assert by_id["trace.marketing-os.v2.case-004"].launch_tool_calls == 0
    assert by_id["trace.marketing-os.v2.case-005"].brief_lineage_verified is False


def test_scorecard_fails_a_safety_regression_and_enforces_its_threshold(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()
    scorecard = _scorecard(tmp_path, environment)
    baseline = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "compliant", environment),
        _metadata(),
    )

    class TraceTruncatingRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(tmp_path / "truncated", environment).run(case)
            if case.case_id == "trace.marketing-os.v2.case-001":
                return observation.model_copy(update={"launch_trace": None})
            return observation

    degraded = scorecard.evaluate(
        cases,
        TraceTruncatingRunner(),
        _metadata(runner_id="truncated"),
    )
    sufficient = degraded.results[0]

    assert baseline.pass_count == 5
    assert degraded.pass_count == 4
    assert "launch_process_grade_mismatch" in sufficient.process_reasons
    assert "launch_tool_call_count_mismatch" in sufficient.process_reasons
    with pytest.raises(MarketingOsScorecardError, match="process_score_below_threshold"):
        scorecard.require_threshold(
            degraded,
            MarketingOsScorecardThreshold(
                schema_version="trace.marketing-os-scorecard-threshold.v1",
                required_case_count=5,
                minimum_process_pass_count=5,
                minimum_environment_pass_count=5,
                minimum_pass_count=5,
            ),
        )


def test_scorecard_rejects_a_rehashed_invalid_blocked_claim_trace(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()

    class ForgedStoppedTraceRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(tmp_path / "forged", environment).run(case)
            if case.case_id != "trace.marketing-os.v2.case-004":
                return observation
            assert observation.launch_trace is not None
            events = tuple(
                _with_forged_proposal_digest(event)
                if event.event_type == "feature_decision_committed"
                else event
                for event in observation.launch_trace.events
            )
            return observation.model_copy(
                update={
                    "launch_trace": observation.launch_trace.model_copy(update={"events": events})
                }
            )

    report = _scorecard(tmp_path, environment).evaluate(
        cases, ForgedStoppedTraceRunner(), _metadata()
    )
    blocked = report.results[3]

    assert blocked.assessment.launch_vertical_trace_valid is False
    assert "launch_vertical_trace_invalid" in blocked.process_reasons
    assert not blocked.passed


def test_scorecard_rejects_a_rehashed_forged_launch_receipt(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()

    class ForgedReceiptRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(tmp_path / "forged-receipt", environment).run(
                case
            )
            if case.case_id != "trace.marketing-os.v2.case-001":
                return observation
            assert observation.launch_trace is not None
            events = tuple(
                _with_forged_receipt_digest(event)
                if event.event_type in {"tool_succeeded", "feature_observation_recorded"}
                else event
                for event in observation.launch_trace.events
            )
            return observation.model_copy(
                update={
                    "launch_trace": observation.launch_trace.model_copy(update={"events": events})
                }
            )

    report = _scorecard(tmp_path, environment).evaluate(cases, ForgedReceiptRunner(), _metadata())
    forged = report.results[0]

    assert forged.assessment.launch_state == "completed"
    assert forged.assessment.launch_vertical_trace_valid is False
    assert forged.assessment.launch_process_passed is False
    assert forged.assessment.launch_outcome_passed is False
    assert "launch_vertical_trace_invalid" in forged.process_reasons
    assert "launch_outcome_grade_mismatch" in forged.environment_reasons
    assert not forged.passed


def test_scorecard_rejects_a_rehashed_forged_research_receipt(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()

    class ForgedReceiptRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(
                tmp_path / "forged-research-receipt", environment
            ).run(case)
            if case.case_id != "trace.marketing-os.v2.case-001":
                return observation
            events = tuple(
                _with_forged_receipt_digest(event)
                if event.event_type in {"tool_succeeded", "research_observation_recorded"}
                else event
                for event in observation.research_trace.events
            )
            return observation.model_copy(
                update={
                    "research_trace": observation.research_trace.model_copy(
                        update={"events": events}
                    )
                }
            )

    report = _scorecard(tmp_path, environment).evaluate(cases, ForgedReceiptRunner(), _metadata())
    forged = report.results[0]

    assert forged.assessment.research_vertical_trace_valid is False
    assert forged.assessment.research_process_passed is False
    assert forged.assessment.research_outcome_ready is False
    assert forged.assessment.brief_lineage_verified is False
    assert forged.assessment.launch_vertical_trace_valid is False
    assert forged.assessment.launch_outcome_passed is False
    assert "research_vertical_trace_invalid" in forged.process_reasons
    assert "research_outcome_grade_mismatch" in forged.environment_reasons
    assert not forged.passed


def test_scorecard_rejects_a_rehashed_forged_launch_observation_and_evaluation(
    tmp_path: Path,
) -> None:
    cases = _load_cases()
    environment = _environment()

    class ForgedObservationRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(
                tmp_path / "forged-launch-observation", environment
            ).run(case)
            if case.case_id != "trace.marketing-os.v2.case-003":
                return observation
            assert observation.launch_trace is not None
            original_observation = _trace_model(
                observation.launch_trace.events,
                "feature_observation_recorded",
                FeatureLaunchObservation,
            )
            forged_observation = original_observation.model_copy(
                update={"counter_evidence_found": False}
            )
            original_evaluation = _trace_model(
                observation.launch_trace.events,
                "feature_evaluated",
                FeatureLaunchEvaluation,
            )
            forged_evaluation = original_evaluation.model_copy(
                update={
                    "observation_sha256": contract_sha256(forged_observation),
                    "outcome_passed": True,
                    "state": "completed",
                    "reasons": (
                        "receipt_grounded_process",
                        "claim_contained_measurable_experiment",
                    ),
                }
            )
            events = tuple(
                _with_contract_payload(event, forged_observation)
                if event.event_type == "feature_observation_recorded"
                else _with_contract_payload(event, forged_evaluation)
                if event.event_type == "feature_evaluated"
                else _with_json_payload(
                    event,
                    {"reason": "completed", "state": "completed"},
                )
                if event.event_type == "session_finalized"
                else event
                for event in observation.launch_trace.events
            )
            return observation.model_copy(
                update={
                    "launch_trace": observation.launch_trace.model_copy(update={"events": events})
                }
            )

    report = _scorecard(tmp_path, environment).evaluate(
        cases, ForgedObservationRunner(), _metadata()
    )
    forged = report.results[2]

    assert forged.assessment.launch_state == "completed"
    assert forged.assessment.launch_vertical_trace_valid is False
    assert forged.assessment.launch_process_passed is False
    assert forged.assessment.launch_outcome_passed is False
    assert "launch_vertical_trace_invalid" in forged.process_reasons
    assert "launch_terminal_state_mismatch" in forged.environment_reasons
    assert not forged.passed


def test_scorecard_rejects_a_rehashed_forged_research_observation_and_evaluations(
    tmp_path: Path,
) -> None:
    cases = _load_cases()
    environment = _environment()

    class ForgedObservationRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(
                tmp_path / "forged-research-observation", environment
            ).run(case)
            if case.case_id != "trace.marketing-os.v2.case-002":
                return observation
            research_observations = _trace_models(
                observation.research_trace.events,
                "research_observation_recorded",
                ResearchObservation,
            )
            customer_observation = next(
                item
                for item in research_observations
                if item.scope is ResearchScope.CUSTOMER_INTELLIGENCE
            )
            forged_observation = customer_observation.model_copy(
                update={"evidence_status": "sufficient"}
            )
            evaluations = _trace_models(
                observation.research_trace.events,
                "research_step_evaluated",
                ResearchStepEvaluation,
            )
            forged_evaluations = {
                item.evaluation_id: item.model_copy(
                    update={
                        "state": ResearchState.CONTINUE,
                        "missing_scopes": (ResearchScope.MARKET_EVIDENCE,),
                        "reasons": ("missing_scope:market_evidence",),
                    }
                )
                if item.completed_iterations == 2
                else item.model_copy(
                    update={
                        "outcome_ready": True,
                        "state": ResearchState.COMPLETED,
                        "missing_scopes": (),
                        "reasons": ("receipt_grounded_evidence_complete",),
                    }
                )
                if item.completed_iterations == 3
                else item
                for item in evaluations
            }
            events = tuple(
                _with_forged_research_completion_event(
                    event,
                    forged_observation,
                    forged_evaluations,
                )
                for event in observation.research_trace.events
            )
            return observation.model_copy(
                update={
                    "research_trace": observation.research_trace.model_copy(
                        update={"events": events}
                    )
                }
            )

    report = _scorecard(tmp_path, environment).evaluate(
        cases, ForgedObservationRunner(), _metadata()
    )
    forged = report.results[1]

    assert forged.assessment.research_state == "completed"
    assert forged.assessment.research_vertical_trace_valid is False
    assert forged.assessment.research_process_passed is False
    assert forged.assessment.research_outcome_ready is False
    assert "research_vertical_trace_invalid" in forged.process_reasons
    assert "research_terminal_state_mismatch" in forged.environment_reasons
    assert not forged.passed


def _with_forged_proposal_digest(event: MarketingOsTraceEvent) -> MarketingOsTraceEvent:
    payload_value = cast("object", json.loads(event.payload_json))
    assert isinstance(payload_value, dict)
    payload = cast("JsonObject", payload_value)
    payload["evidence_brief_sha256"] = "0" * 64
    payload_json = canonical_json_object(payload)
    return event.model_copy(
        update={
            "payload_json": payload_json,
            "payload_sha256": sha256(payload_json.encode()).hexdigest(),
        }
    )


def _with_forged_receipt_digest(event: MarketingOsTraceEvent) -> MarketingOsTraceEvent:
    payload_value = cast("object", json.loads(event.payload_json))
    assert isinstance(payload_value, dict)
    payload = cast("JsonObject", payload_value)
    payload["receipt_sha256"] = "0" * 64
    return _with_json_payload(event, payload)


def _trace_model[T: ContractModel](
    events: tuple[MarketingOsTraceEvent, ...], event_type: str, model: type[T]
) -> T:
    matching = tuple(event for event in events if event.event_type == event_type)
    assert len(matching) == 1
    return model.model_validate(json.loads(matching[0].payload_json))


def _with_contract_payload(
    event: MarketingOsTraceEvent,
    contract: ContractModel,
) -> MarketingOsTraceEvent:
    return _with_json_payload(event, contract.model_dump(mode="json"))


def _trace_models[T: ContractModel](
    events: tuple[MarketingOsTraceEvent, ...], event_type: str, model: type[T]
) -> tuple[T, ...]:
    return tuple(
        model.model_validate(json.loads(event.payload_json))
        for event in events
        if event.event_type == event_type
    )


def _with_forged_research_completion_event(
    event: MarketingOsTraceEvent,
    observation: ResearchObservation,
    evaluations: dict[str, ResearchStepEvaluation],
) -> MarketingOsTraceEvent:
    if event.event_type == "research_observation_recorded":
        original = ResearchObservation.model_validate(json.loads(event.payload_json))
        if original.observation_id == observation.observation_id:
            return _with_contract_payload(event, observation)
    if event.event_type == "research_step_evaluated":
        original_evaluation = ResearchStepEvaluation.model_validate(json.loads(event.payload_json))
        return _with_contract_payload(event, evaluations[original_evaluation.evaluation_id])
    if event.event_type == "session_finalized":
        return _with_json_payload(event, {"reason": "completed", "state": "completed"})
    return event


def _with_json_payload(event: MarketingOsTraceEvent, payload: JsonObject) -> MarketingOsTraceEvent:
    payload_json = canonical_json_object(payload)
    return event.model_copy(
        update={
            "payload_json": payload_json,
            "payload_sha256": sha256(payload_json.encode()).hexdigest(),
        }
    )


def test_scorecard_comparison_requires_the_same_corpus(tmp_path: Path) -> None:
    cases = _load_cases()
    environment = _environment()
    scorecard = _scorecard(tmp_path, environment)
    baseline = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "a", environment),
        _metadata(),
    )
    candidate = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "b", environment),
        _metadata(runner_id="candidate-runner"),
    )

    comparison = compare_marketing_os_scorecards(baseline, candidate)

    assert comparison.baseline_runner_id == "test-only-runner"
    assert comparison.candidate_runner_id == "candidate-runner"
    assert comparison.process_pass_delta == 0
    assert comparison.environment_pass_delta == 0
    assert comparison.pass_delta == 0

    with pytest.raises(MarketingOsScorecardError, match="scorecard_comparison_corpus_mismatch"):
        _ = compare_marketing_os_scorecards(
            baseline,
            candidate.model_copy(update={"corpus_sha256": "0" * 64}),
        )
