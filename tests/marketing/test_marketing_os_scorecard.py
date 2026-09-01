"""Regression tests for the offline, versioned Marketing OS scorecard."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pytest

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


def _scorecard(root: Path) -> MarketingOsScorecard:
    return MarketingOsScorecard(TestOnlyMarketingOsTraceVerifier(root / "verifier"))


def _environment() -> FixtureEnvironment:
    raw_cases = cast(
        "dict[str, dict[str, object]]", _load_fixture("tool_environment.json")["cases"]
    )
    return FixtureEnvironment(
        {
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
        }
    )


def test_versioned_marketing_os_scorecard_grades_real_multiskill_paths(tmp_path: Path) -> None:
    cases = _load_cases()
    runner = TestOnlyMarketingOsRunner(tmp_path, _environment())

    report = _scorecard(tmp_path).evaluate(cases, runner, _metadata())

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
    scorecard = _scorecard(tmp_path)
    baseline = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "compliant", _environment()),
        _metadata(),
    )

    class TraceTruncatingRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(tmp_path / "truncated", _environment()).run(
                case
            )
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

    class ForgedStoppedTraceRunner:
        def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation:
            observation = TestOnlyMarketingOsRunner(tmp_path / "forged", _environment()).run(case)
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

    report = _scorecard(tmp_path).evaluate(cases, ForgedStoppedTraceRunner(), _metadata())
    blocked = report.results[3]

    assert blocked.assessment.launch_vertical_trace_valid is False
    assert "launch_vertical_trace_invalid" in blocked.process_reasons
    assert not blocked.passed


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


def test_scorecard_comparison_requires_the_same_corpus(tmp_path: Path) -> None:
    cases = _load_cases()
    scorecard = _scorecard(tmp_path)
    baseline = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "a", _environment()),
        _metadata(),
    )
    candidate = scorecard.evaluate(
        cases,
        TestOnlyMarketingOsRunner(tmp_path / "b", _environment()),
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
