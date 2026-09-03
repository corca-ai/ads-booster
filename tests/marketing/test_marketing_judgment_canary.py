from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.marketing_agent import (
    ExperimentEvaluation,
    MarketingReassessment,
    StrategyBrief,
    contract_sha256,
)
from ads_booster.marketing.decision_quality import DecisionQualityScenario
from ads_booster.marketing.hosted_reassessment_judgment import OutcomeReassessmentRequest
from ads_booster.marketing.marketing_judgment_canary import (
    ExpectedEvidenceDisposition,
    ExpectedHypothesisDisposition,
    HostedReassessmentTrialRunner,
    MarketingJudgmentCanaryCase,
    MarketingJudgmentCanaryError,
    MarketingJudgmentCanaryExpectation,
    MarketingJudgmentCanaryInput,
    MarketingJudgmentRuntimeIdentity,
    MarketingJudgmentTrialObservation,
    SemanticAnchor,
    build_hosted_reassessment_trial_runner,
    evaluate_marketing_judgment_canary,
)
from ads_booster.marketing.marketing_judgment_canary_corpus import (
    load_private_marketing_judgment_canary_cases,
)
from ads_booster.providers.codex_cli import CodexCli

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _request(*, outcome: str) -> OutcomeReassessmentRequest:
    inconclusive = outcome == "inconclusive"
    evaluation = {
        "schema_version": "trace.experiment-evaluation.v1",
        "evaluation_id": "evaluation-1",
        "campaign_id": "campaign-1",
        "experiment_id": "experiment-1",
        "state": "inconclusive" if inconclusive else "evaluated",
        "outcome_scope": "direct_response_attribution",
        "eligible_blocks": 1 if inconclusive else 2,
        "attribution_coverage_basis_points": 5000 if inconclusive else 10000,
        "winner_hypothesis_id": None if inconclusive else "challenger",
        "causal_estimate": None,
        "interpretation": (
            "The minimum eligible blocks and attribution coverage were not reached."
            if inconclusive
            else "The challenger has the highest observed attributed rate."
        ),
        "guardrail_failures": [],
        "lineage_ids": ["assignment-1", "assignment-2"],
        "evaluated_at": NOW,
    }
    prior = {
        "schema_version": "trace.strategy-brief.v1",
        "brief_id": "brief-1",
        "campaign_id": "campaign-1",
        "account_id": "trace_kr",
        "feature_packet_id": "packet-1",
        "feature_packet_sha256": "a" * 64,
        "context_receipt_sha256": "b" * 64,
        "business_outcome": "Increase completed lock-screen setups.",
        "audience_situation": "An iPhone user wants one character through the day.",
        "belief_to_change": "A lock screen can evolve instead of staying static.",
        "decision_dossier": {
            "schema_version": "trace.marketing-decision-dossier.v1",
            "situation": "new_launch",
            "selected_icp_id": "ios-character-fans",
            "selection_basis_ids": ["signal-1"],
            "positioning": {
                "category": "dynamic lock-screen companion",
                "current_alternative": "one static wallpaper",
                "differentiated_mechanism": "scheduled character scenes change through the day",
                "proof_claim_ids": ["claim-1"],
            },
            "evidence_dispositions": [
                {
                    "evidence_id": "signal-1",
                    "disposition": "supports",
                    "confidence_basis_points": 7000,
                    "freshness": "fresh",
                    "use": "use_as_constraint",
                    "reason": "An approved signal supports the audience.",
                }
            ],
            "recommended_next_step": "design_experiment",
            "reason": "The signal supports one bounded experiment.",
            "required_proof_ids": ["claim-1"],
        },
        "hypotheses": [
            {
                "hypothesis_id": "control",
                "role": "control",
                "claim_ids": ["claim-1"],
                "value_frame": "static utility hook",
                "rationale": "Preserve the baseline.",
                "falsifier": "It produces no setup completions.",
                "proof_requirement": "Show installed scheduled scenes.",
                "conversation_motive": "Ask which scene viewers want.",
                "reference_ids": [],
            },
            {
                "hypothesis_id": "challenger",
                "role": "challenger",
                "claim_ids": ["claim-1"],
                "value_frame": "character continuity hook",
                "rationale": "Continuity may clarify the feature.",
                "falsifier": "It does not beat the control.",
                "proof_requirement": "Show one character across scenes.",
                "conversation_motive": "Ask which character should appear next.",
                "reference_ids": [],
            },
        ],
        "experiment": {
            "experiment_id": "experiment-1",
            "manipulated_component": "value frame",
            "held_constant_components": ["account", "posting slot"],
            "allowed_incidental_differences": [],
            "activated_hypothesis_ids": ["control", "challenger"],
            "primary_outcome": {
                "name": "setup_completed",
                "scope": "direct_response_attribution",
                "window_hours": 72,
                "causal_estimand": None,
            },
            "diagnostic_metrics": ["views"],
            "guardrails": ["product fidelity"],
            "minimum_eligible_blocks": 2,
            "maximum_posts": 4,
            "maximum_duration_hours": 336,
            "minimum_attribution_coverage_basis_points": 8000,
            "stop_rules": ["stop on a fidelity violation"],
            "inconclusive_when": ["minimum blocks are not reached"],
        },
        "created_at": NOW,
    }
    prior_strategy = StrategyBrief.model_validate(prior)
    observed_evaluation = ExperimentEvaluation.model_validate(evaluation)
    return OutcomeReassessmentRequest.model_validate(
        {
            "pipeline": "hosted_marketing_judgment_v1",
            "judgment": "outcome_reassessment",
            "reassessment_id": f"reassessment-{outcome}",
            "campaign_id": "campaign-1",
            "account_id": "trace_kr",
            "situation": "experiment_result",
            "prior_strategy": prior_strategy,
            "prior_strategy_sha256": contract_sha256(prior_strategy),
            "evaluation": observed_evaluation,
            "evaluation_sha256": contract_sha256(observed_evaluation),
            "supported_claim_ids": ["claim-1"],
            "requested_by": "hosted_workspace",
        }
    )


def _reassessment(
    request: OutcomeReassessmentRequest,
    *,
    force_outcome: str | None = None,
) -> MarketingReassessment:
    inconclusive = (force_outcome or request.evaluation.state) == "inconclusive"
    dossier = request.prior_strategy.decision_dossier
    assert dossier is not None
    return MarketingReassessment.model_validate(
        {
            "schema_version": "trace.marketing-reassessment.v1",
            "reassessment_id": request.reassessment_id,
            "campaign_id": request.campaign_id,
            "trigger_evaluation_id": request.evaluation.evaluation_id,
            "trigger_evaluation_sha256": request.evaluation_sha256,
            "situation": request.situation,
            "decision_dossier": {
                "schema_version": "trace.marketing-decision-dossier.v1",
                "situation": request.situation,
                "selected_icp_id": "ios-character-fans",
                "selection_basis_ids": ["signal-1"],
                "positioning": dossier.positioning.model_dump(mode="json"),
                "evidence_dispositions": [
                    dossier.evidence_dispositions[0].model_dump(mode="json"),
                    {
                        "evidence_id": request.evaluation.evaluation_id,
                        "disposition": "insufficient" if inconclusive else "supports",
                        "confidence_basis_points": 10000,
                        "freshness": "fresh",
                        "use": "use_as_constraint",
                        "reason": (
                            "The low-coverage result is insufficient for a directional conclusion."
                            if inconclusive
                            else "The observed challenger result supports a follow-up replication."
                        ),
                    },
                ],
                "recommended_next_step": "hold_for_review" if inconclusive else "design_experiment",
                "reason": (
                    "Hold because minimum blocks and attribution coverage are insufficient."
                    if inconclusive
                    else "Design a bounded follow-up replication of the observed challenger signal."
                ),
                "required_proof_ids": ["claim-1", request.evaluation.evaluation_id],
            },
            "hypothesis_reassessments": [
                {
                    "hypothesis_id": "control",
                    "disposition": "retain",
                    "rationale": "Retain the stable comparison.",
                    "next_test": None if inconclusive else "Keep the control unchanged.",
                },
                {
                    "hypothesis_id": "challenger",
                    "disposition": "retain" if inconclusive else "revise",
                    "rationale": (
                        "Retain it until the registered sample and coverage thresholds are met."
                        if inconclusive
                        else "Revise one value-frame sentence for a narrower replication."
                    ),
                    "next_test": None
                    if inconclusive
                    else "Change only the first value-frame sentence.",
                },
            ],
            "unanswered_questions": [
                "Will another eligible block raise attribution coverage above the threshold?"
                if inconclusive
                else "Will the challenger direction replicate in another complete block?"
            ],
            "created_at": request.evaluation.evaluated_at,
        }
    )


def _case(case_id: str, *, outcome: str) -> MarketingJudgmentCanaryCase:
    request = _request(outcome=outcome)
    inconclusive = outcome == "inconclusive"
    return MarketingJudgmentCanaryCase(
        MarketingJudgmentCanaryInput(
            schema_version="trace.marketing-judgment-canary-input.v1",
            case_id=case_id,
            request=request,
        ),
        MarketingJudgmentCanaryExpectation(
            schema_version="trace.marketing-judgment-canary-expectation.v1",
            case_id=case_id,
            decision_scenario=DecisionQualityScenario.model_validate(
                {
                    "schema_version": "trace.marketing-decision-quality-scenario.v1",
                    "scenario_id": case_id,
                    "situation": "experiment_result",
                    "supported_claim_ids": ["claim-1"],
                    "allowed_icp_ids": ["ios-character-fans"],
                    "required_icp_basis_ids": ["signal-1"],
                    "required_evidence_ids": ["signal-1", "evaluation-1"],
                    "allowed_next_steps": [
                        "hold_for_review" if inconclusive else "design_experiment"
                    ],
                }
            ),
            evidence_directions=(
                ExpectedEvidenceDisposition(
                    evidence_id="evaluation-1",
                    disposition="insufficient" if inconclusive else "supports",
                    use="use_as_constraint",
                ),
            ),
            hypothesis_directions=(
                ExpectedHypothesisDisposition(
                    hypothesis_id="control",
                    disposition="retain",
                    next_test_required=not inconclusive,
                ),
                ExpectedHypothesisDisposition(
                    hypothesis_id="challenger",
                    disposition="retain" if inconclusive else "revise",
                    next_test_required=not inconclusive,
                ),
            ),
            semantic_anchors=(
                SemanticAnchor(
                    anchor_id=f"{case_id}-reason",
                    field="decision_reason",
                    any_of=("minimum blocks", "coverage")
                    if inconclusive
                    else ("follow-up", "replication"),
                ),
                SemanticAnchor(
                    anchor_id=f"{case_id}-detail",
                    field="unanswered_questions" if inconclusive else "next_tests",
                    any_of=("another eligible block",) if inconclusive else ("change only",),
                ),
            ),
            forbidden_phrases=("grader-secret-sentinel",),
            counterfactual_pair_id="same-situation-outcome-evidence",
            required_pair_differences=(
                "recommended_next_step",
                "hypothesis_dispositions",
                "evidence_dispositions",
                "unanswered_questions",
            ),
        ),
    )


def _runtime() -> MarketingJudgmentRuntimeIdentity:
    return MarketingJudgmentRuntimeIdentity(
        schema_version="trace.marketing-judgment-runtime.v1",
        provider_id="fixture",
        requested_model_id="fixture-model",
        executable_name="fixture-codex",
        executable_sha256="a" * 64,
        executable_version="fixture-codex 1.0",
        package_version="0.0.0",
    )


@dataclass(slots=True)
class StubRunner:
    outputs: dict[str, MarketingReassessment]
    runtime_identity: MarketingJudgmentRuntimeIdentity = field(default_factory=_runtime)
    seen: list[MarketingJudgmentCanaryInput] = field(default_factory=list)

    def run(
        self,
        case: MarketingJudgmentCanaryInput,
        *,
        trial: int,
    ) -> MarketingJudgmentTrialObservation:
        self.seen.append(case)
        reassessment = self.outputs[case.case_id]
        nonce = _digest({"case_id": case.case_id, "trial": trial})
        return MarketingJudgmentTrialObservation(
            schema_version="trace.marketing-judgment-trial-observation.v1",
            case_id=case.case_id,
            trial=trial,
            trial_nonce_sha256=nonce,
            prompt_sha256="b" * 64,
            output_schema_sha256="c" * 64,
            elapsed_milliseconds=1,
            state="succeeded",
            reassessment=reassessment,
            reassessment_sha256=contract_sha256(reassessment),
        )


def test_counterfactual_canary_passes_only_dynamic_outcome_responses() -> None:
    cases = (
        _case("challenger-result", outcome="evaluated"),
        _case("inconclusive-result", outcome="inconclusive"),
    )
    runner = StubRunner({case.input.case_id: _reassessment(case.input.request) for case in cases})

    report = evaluate_marketing_judgment_canary(cases, runner, trials=2)

    assert report.all_trials_passed is True
    assert report.pass_count == 4
    assert report.pair_pass_count == 2
    assert all(
        item.observed_differences == item.required_differences for item in report.pair_results
    )
    assert all(isinstance(item, MarketingJudgmentCanaryInput) for item in runner.seen)


def test_fixed_router_negative_control_fails_the_counterfactual_pair() -> None:
    cases = (
        _case("challenger-result", outcome="evaluated"),
        _case("inconclusive-result", outcome="inconclusive"),
    )
    runner = StubRunner(
        {
            case.input.case_id: _reassessment(case.input.request, force_outcome="evaluated")
            for case in cases
        }
    )

    report = evaluate_marketing_judgment_canary(cases, runner, trials=2)

    assert report.all_trials_passed is False
    assert report.pass_count == 2
    assert report.pair_pass_count == 0
    assert "evidence_direction:evaluation-1" in report.results[1].failure_codes
    assert "hypothesis_direction:challenger" in report.results[1].failure_codes
    assert report.pair_results[0].failure_codes


def test_direction_only_mismatch_is_a_failed_result_instead_of_a_canary_crash() -> None:
    first = _case("challenger-result", outcome="evaluated")
    second = _case("inconclusive-result", outcome="inconclusive")
    wrong_direction = first.expectation.model_copy(
        update={
            "evidence_directions": (
                ExpectedEvidenceDisposition(
                    evidence_id="evaluation-1",
                    disposition="contradicts",
                    use="use_as_constraint",
                ),
            )
        }
    )
    cases = (MarketingJudgmentCanaryCase(first.input, wrong_direction), second)
    runner = StubRunner({case.input.case_id: _reassessment(case.input.request) for case in cases})

    report = evaluate_marketing_judgment_canary(cases, runner, trials=2)

    failed = report.results[0]
    assert failed.process_passed is True
    assert failed.decision_quality is not None
    assert failed.decision_quality.passed is True
    assert failed.semantic_quality is not None
    assert failed.semantic_quality.passed is True
    assert failed.passed is False
    assert failed.failure_codes == ("evidence_direction:evaluation-1",)
    assert report.all_trials_passed is False


def test_counterfactual_pair_rejects_changed_context() -> None:
    first = _case("challenger-result", outcome="evaluated")
    second = _case("inconclusive-result", outcome="inconclusive")
    changed_request = second.input.request.model_copy(update={"account_id": "other-account"})
    changed_input = second.input.model_copy(update={"request": changed_request})
    changed_case = MarketingJudgmentCanaryCase(changed_input, second.expectation)
    cases = (first, changed_case)
    runner = StubRunner({case.input.case_id: _reassessment(case.input.request) for case in cases})

    with pytest.raises(MarketingJudgmentCanaryError, match="pair_context_mismatch"):
        _ = evaluate_marketing_judgment_canary(cases, runner, trials=2)


def test_counterfactual_pair_requires_different_outcome_evidence() -> None:
    first = _case("challenger-result", outcome="evaluated")
    second = _case("inconclusive-result", outcome="inconclusive")
    unchanged_request = second.input.request.model_copy(
        update={
            "evaluation": first.input.request.evaluation,
            "evaluation_sha256": first.input.request.evaluation_sha256,
        }
    )
    unchanged_input = second.input.model_copy(update={"request": unchanged_request})
    unchanged_case = MarketingJudgmentCanaryCase(unchanged_input, second.expectation)
    cases = (first, unchanged_case)
    runner = StubRunner({case.input.case_id: _reassessment(case.input.request) for case in cases})

    with pytest.raises(MarketingJudgmentCanaryError, match="pair_evidence_not_perturbed"):
        _ = evaluate_marketing_judgment_canary(cases, runner, trials=2)


def test_counterfactual_does_not_count_question_reordering_as_a_decision_change() -> None:
    cases = (
        _case("challenger-result", outcome="evaluated"),
        _case("inconclusive-result", outcome="inconclusive"),
    )
    left = _reassessment(cases[0].input.request).model_copy(
        update={"unanswered_questions": ("Question A?", "Question B?")}
    )
    right = _reassessment(cases[1].input.request, force_outcome="evaluated").model_copy(
        update={"unanswered_questions": ("Question B?", "Question A?")}
    )
    runner = StubRunner(
        {
            cases[0].input.case_id: left,
            cases[1].input.case_id: right,
        }
    )

    report = evaluate_marketing_judgment_canary(cases, runner, trials=2)

    assert "unanswered_questions" not in report.pair_results[0].observed_differences


def test_canary_requires_repeated_trials() -> None:
    cases = (
        _case("challenger-result", outcome="evaluated"),
        _case("inconclusive-result", outcome="inconclusive"),
    )
    runner = StubRunner({case.input.case_id: _reassessment(case.input.request) for case in cases})

    with pytest.raises(MarketingJudgmentCanaryError, match="trial_count_invalid"):
        _ = evaluate_marketing_judgment_canary(cases, runner, trials=1)


@dataclass(slots=True)
class ProposalCodex:
    proposal: JsonObject
    prompts: list[str] = field(default_factory=list)
    workspaces: list[Path] = field(default_factory=list)

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        assert schema["type"] == "object"
        assert timeout_seconds == 240
        self.prompts.append(prompt)
        self.workspaces.append(workspace)
        return self.proposal


def test_hosted_runner_uses_fresh_workspaces_without_grader_expectations(tmp_path: Path) -> None:
    case = _case("challenger-result", outcome="evaluated")
    reassessment = _reassessment(case.input.request)
    proposal = reassessment.model_dump(mode="json")
    for key in (
        "reassessment_id",
        "campaign_id",
        "trigger_evaluation_id",
        "trigger_evaluation_sha256",
        "situation",
        "created_at",
    ):
        del proposal[key]
    proposal["schema_version"] = "trace.outcome-reassessment-proposal.v1"
    codex = ProposalCodex(proposal)
    runner = HostedReassessmentTrialRunner(codex, tmp_path, _runtime())

    first = runner.run(case.input, trial=1)
    second = runner.run(case.input, trial=2)

    assert first.state == second.state == "succeeded"
    assert first.trial_nonce_sha256 != second.trial_nonce_sha256
    assert codex.workspaces[0] != codex.workspaces[1]
    assert "grader-secret-sentinel" not in " ".join(codex.prompts)


def test_concrete_runner_binds_the_inspected_codex_executable_and_requested_model(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "codex"
    _ = executable.write_text("#!/bin/sh\nprintf 'codex-cli 1.2.3\\n'\n", encoding="utf-8")
    executable.chmod(0o700)

    runner = build_hosted_reassessment_trial_runner(
        executable,
        model_id="gpt-test",
        output_root=tmp_path / "runs",
    )

    assert isinstance(runner.codex, CodexCli)
    assert runner.codex.executable == executable
    assert runner.codex.model == "gpt-test"
    assert runner.runtime_identity.requested_model_id == "gpt-test"
    assert runner.runtime_identity.executable_version == "codex-cli 1.2.3"


def test_private_corpus_loader_keeps_runner_and_grader_files_paired(tmp_path: Path) -> None:
    cases = (
        _case("challenger-result", outcome="evaluated"),
        _case("inconclusive-result", outcome="inconclusive"),
    )
    _ = (tmp_path / "runner_inputs.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.marketing-judgment-canary-inputs.v1",
                "cases": [case.input.model_dump(mode="json") for case in cases],
            }
        )
    )
    _ = (tmp_path / "grader_expectations.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.marketing-judgment-canary-expectations.v1",
                "cases": [case.expectation.model_dump(mode="json") for case in reversed(cases)],
            }
        )
    )

    loaded = load_private_marketing_judgment_canary_cases(tmp_path)

    assert tuple(case.input.case_id for case in loaded) == (
        "challenger-result",
        "inconclusive-result",
    )
    assert tuple(case.expectation.case_id for case in loaded) == (
        "challenger-result",
        "inconclusive-result",
    )


def test_private_corpus_loader_rejects_mismatched_case_sets(tmp_path: Path) -> None:
    first = _case("challenger-result", outcome="evaluated")
    second = _case("inconclusive-result", outcome="inconclusive")
    _write_private_corpus(tmp_path, (first,), (second.expectation,))

    with pytest.raises(MarketingJudgmentCanaryError, match="case_sets_mismatch"):
        _ = load_private_marketing_judgment_canary_cases(tmp_path)


def test_private_corpus_loader_rejects_duplicate_runner_ids(tmp_path: Path) -> None:
    first = _case("challenger-result", outcome="evaluated")
    _write_private_corpus(tmp_path, (first, first), (first.expectation,))

    with pytest.raises(MarketingJudgmentCanaryError, match="duplicate_runner_input"):
        _ = load_private_marketing_judgment_canary_cases(tmp_path)


def test_private_corpus_loader_rejects_a_symlinked_file_outside_its_root(tmp_path: Path) -> None:
    case = _case("challenger-result", outcome="evaluated")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-inputs.json"
    _ = outside.write_text(
        json.dumps(
            {
                "schema_version": "trace.marketing-judgment-canary-inputs.v1",
                "cases": [case.input.model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runner_inputs.json").symlink_to(outside)
    _write_private_corpus(tmp_path, (), (case.expectation,), write_inputs=False)

    with pytest.raises(MarketingJudgmentCanaryError, match="outside_root"):
        _ = load_private_marketing_judgment_canary_cases(tmp_path)


def _write_private_corpus(
    root: Path,
    inputs: tuple[MarketingJudgmentCanaryCase, ...],
    expectations: tuple[MarketingJudgmentCanaryExpectation, ...],
    *,
    write_inputs: bool = True,
) -> None:
    if write_inputs:
        _ = (root / "runner_inputs.json").write_text(
            json.dumps(
                {
                    "schema_version": "trace.marketing-judgment-canary-inputs.v1",
                    "cases": [case.input.model_dump(mode="json") for case in inputs],
                }
            ),
            encoding="utf-8",
        )
    _ = (root / "grader_expectations.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.marketing-judgment-canary-expectations.v1",
                "cases": [expectation.model_dump(mode="json") for expectation in expectations],
            }
        ),
        encoding="utf-8",
    )


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
