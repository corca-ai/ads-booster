"""Private, repeated provider canary for outcome-grounded marketing judgment.

The evaluated runner receives only a frozen reassessment request. Grader expectations stay in a
separate file and combine deterministic contract checks, human-authored semantic anchors, and paired
counterfactual differences. A trusted caller must still isolate the runner and grader processes and
mounts. Reports can become model-quality evidence for one no-tool judgment vertical after real
provider trials; this code alone is not a model, market-lift, or channel-effect result.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self, cast

from pydantic import Field, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    MarketingReassessment,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.decision_quality import (
    DecisionQualityEvaluation,
    DecisionQualityScenario,
    evaluate_decision_quality,
)
from ads_booster.marketing.hosted_reassessment_judgment import (
    HostedOutcomeReassessmentExecutor,
    OutcomeReassessmentRequest,
    StructuredReassessmentJudgment,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

_PACKAGE_NAME = "trace-appium-capture"
_MIN_TRIALS = 2
_MAX_TRIALS = 100
_PAIR_CARDINALITY = 2
_MAX_CASES = 64

SemanticField = Literal[
    "decision_reason",
    "unanswered_questions",
    "hypothesis_rationales",
    "next_tests",
]
PairDifference = Literal[
    "recommended_next_step",
    "hypothesis_dispositions",
    "evidence_dispositions",
    "unanswered_questions",
]


class MarketingJudgmentCanaryError(ValueError):
    """The private corpus, runner, or report violates the canary contract."""


class MarketingJudgmentCanaryInput(ContractModel):
    """The complete input visible to the evaluated no-tool judgment runner."""

    schema_version: Literal["trace.marketing-judgment-canary-input.v1"]
    case_id: AgentIdentifier
    request: OutcomeReassessmentRequest


class SemanticAnchor(ContractModel):
    """One blind, human-authored set of acceptable concepts for a specific output field."""

    anchor_id: AgentIdentifier
    field: SemanticField
    any_of: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def require_distinct_nonempty_concepts(self) -> Self:
        normalized = tuple(_normalize_text(item) for item in self.any_of)
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("semantic anchor concepts must be unique and nonempty")
        return self


class ExpectedEvidenceDisposition(ContractModel):
    """Human-authored direction for one evidence item, not merely output inequality."""

    evidence_id: AgentIdentifier
    disposition: Literal["supports", "contradicts", "insufficient"]
    use: Literal["use_as_constraint", "test", "exclude"]


class ExpectedHypothesisDisposition(ContractModel):
    """Human-authored direction for one hypothesis and whether it may advance a next test."""

    hypothesis_id: AgentIdentifier
    disposition: Literal["retain", "revise", "retire"]
    next_test_required: bool


class MarketingJudgmentCanaryExpectation(ContractModel):
    """Private grader material that is never passed to the provider runner."""

    schema_version: Literal["trace.marketing-judgment-canary-expectation.v1"]
    case_id: AgentIdentifier
    decision_scenario: DecisionQualityScenario
    evidence_directions: Annotated[
        tuple[ExpectedEvidenceDisposition, ...], Field(min_length=1, max_length=32)
    ]
    hypothesis_directions: Annotated[
        tuple[ExpectedHypothesisDisposition, ...], Field(min_length=2, max_length=8)
    ]
    semantic_anchors: Annotated[tuple[SemanticAnchor, ...], Field(min_length=1, max_length=16)]
    forbidden_phrases: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    counterfactual_pair_id: AgentIdentifier
    required_pair_differences: Annotated[
        tuple[PairDifference, ...], Field(min_length=1, max_length=4)
    ]

    @model_validator(mode="after")
    def require_bound_private_expectation(self) -> Self:
        if self.decision_scenario.scenario_id != self.case_id:
            raise ValueError("decision scenario must use the canary case ID")
        anchor_ids = tuple(anchor.anchor_id for anchor in self.semantic_anchors)
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("semantic anchor IDs must be unique")
        evidence_ids = tuple(item.evidence_id for item in self.evidence_directions)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("expected evidence directions must be unique")
        if not set(evidence_ids).issubset(self.decision_scenario.required_evidence_ids):
            raise ValueError("expected evidence direction is outside the scenario")
        hypothesis_ids = tuple(item.hypothesis_id for item in self.hypothesis_directions)
        if len(set(hypothesis_ids)) != len(hypothesis_ids):
            raise ValueError("expected hypothesis directions must be unique")
        differences = self.required_pair_differences
        if len(set(differences)) != len(differences):
            raise ValueError("counterfactual differences must be unique")
        forbidden = tuple(_normalize_text(item) for item in self.forbidden_phrases)
        if any(not item for item in forbidden) or len(set(forbidden)) != len(forbidden):
            raise ValueError("forbidden phrases must be unique and nonempty")
        return self


@dataclass(frozen=True, slots=True)
class MarketingJudgmentCanaryCase:
    """One runner input paired with grader-only expectations outside the runner boundary."""

    input: MarketingJudgmentCanaryInput
    expectation: MarketingJudgmentCanaryExpectation

    def __post_init__(self) -> None:
        """Reject a public input/private expectation pair with broken frozen lineage."""
        if self.input.case_id != self.expectation.case_id:
            raise MarketingJudgmentCanaryError("judgment_canary_case_identifier_mismatch")
        request = self.input.request
        scenario = self.expectation.decision_scenario
        if request.situation != scenario.situation:
            raise MarketingJudgmentCanaryError("judgment_canary_situation_mismatch")
        prior = request.prior_strategy.decision_dossier
        if prior is None:
            raise MarketingJudgmentCanaryError("judgment_canary_prior_dossier_missing")
        expected_evidence = {
            *(item.evidence_id for item in prior.evidence_dispositions),
            request.evaluation.evaluation_id,
        }
        if set(scenario.required_evidence_ids) != expected_evidence:
            raise MarketingJudgmentCanaryError("judgment_canary_evidence_contract_mismatch")
        expected_hypotheses = {item.hypothesis_id for item in request.prior_strategy.hypotheses}
        directed_hypotheses = {
            item.hypothesis_id for item in self.expectation.hypothesis_directions
        }
        if directed_hypotheses != expected_hypotheses:
            raise MarketingJudgmentCanaryError("judgment_canary_hypothesis_direction_mismatch")
        if request.evaluation.evaluation_id not in {
            item.evidence_id for item in self.expectation.evidence_directions
        }:
            raise MarketingJudgmentCanaryError("judgment_canary_evaluation_direction_missing")


class MarketingJudgmentRuntimeIdentity(ContractModel):
    """Observed local executable/package identity plus the explicitly requested model."""

    schema_version: Literal["trace.marketing-judgment-runtime.v1"]
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    requested_model_id: Annotated[str, Field(min_length=1, max_length=240)]
    executable_name: Annotated[str, Field(min_length=1, max_length=255)]
    executable_sha256: Sha256Digest
    executable_version: Annotated[str, Field(min_length=1, max_length=500)]
    package_version: Annotated[str, Field(min_length=1, max_length=120)]


class MarketingJudgmentTrialObservation(ContractModel):
    """Provider result and re-derivable prompt/schema provenance for one fresh trial."""

    schema_version: Literal["trace.marketing-judgment-trial-observation.v1"]
    case_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    trial_nonce_sha256: Sha256Digest
    prompt_sha256: Sha256Digest | None
    output_schema_sha256: Sha256Digest | None
    elapsed_milliseconds: Annotated[int, Field(ge=0, le=3_600_000)]
    state: Literal["succeeded", "failed"]
    failure_code: Annotated[str | None, Field(max_length=300)] = None
    reassessment: MarketingReassessment | None = None
    reassessment_sha256: Sha256Digest | None = None

    @model_validator(mode="after")
    def require_terminal_shape(self) -> Self:
        if self.state == "succeeded":
            if (
                self.failure_code is not None
                or self.prompt_sha256 is None
                or self.output_schema_sha256 is None
                or self.reassessment is None
                or self.reassessment_sha256 is None
                or contract_sha256(self.reassessment) != self.reassessment_sha256
            ):
                raise ValueError("successful judgment trial requires one bound reassessment")
        elif (
            self.failure_code is None
            or self.reassessment is not None
            or self.reassessment_sha256 is not None
        ):
            raise ValueError("failed judgment trial requires only one failure code")
        return self


class MarketingJudgmentSemanticEvaluation(ContractModel):
    schema_version: Literal["trace.marketing-judgment-semantic-evaluation.v1"]
    passed: bool
    matched_anchor_ids: tuple[AgentIdentifier, ...]
    missing_anchor_ids: tuple[AgentIdentifier, ...]
    forbidden_phrase_hits: tuple[str, ...]

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> Self:
        if self.passed != (not self.missing_anchor_ids and not self.forbidden_phrase_hits):
            raise ValueError("semantic verdict does not match its evidence")
        return self


class MarketingJudgmentTrialResult(ContractModel):
    schema_version: Literal["trace.marketing-judgment-trial-result.v1"]
    case_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    process_passed: bool
    decision_quality: DecisionQualityEvaluation | None
    semantic_quality: MarketingJudgmentSemanticEvaluation | None
    passed: bool
    failure_codes: tuple[str, ...] = ()
    observation: MarketingJudgmentTrialObservation

    @model_validator(mode="after")
    def require_combined_verdict(self) -> Self:
        expected = bool(
            self.process_passed
            and self.decision_quality is not None
            and self.decision_quality.passed
            and self.semantic_quality is not None
            and self.semantic_quality.passed
            and not self.failure_codes
        )
        if self.passed != expected:
            raise ValueError("trial verdict does not match process and quality grades")
        return self


class MarketingJudgmentPairResult(ContractModel):
    schema_version: Literal["trace.marketing-judgment-pair-result.v1"]
    pair_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    case_ids: Annotated[tuple[AgentIdentifier, AgentIdentifier], Field(min_length=2, max_length=2)]
    required_differences: tuple[PairDifference, ...]
    observed_differences: tuple[PairDifference, ...]
    passed: bool
    failure_codes: tuple[str, ...] = ()


class MarketingJudgmentCanaryReport(ContractModel):
    schema_version: Literal["trace.marketing-judgment-canary-report.v1"]
    corpus_sha256: Sha256Digest
    runtime: MarketingJudgmentRuntimeIdentity
    trial_count: Annotated[int, Field(ge=_MIN_TRIALS, le=_MAX_TRIALS)]
    results: Annotated[
        tuple[MarketingJudgmentTrialResult, ...], Field(min_length=2, max_length=6400)
    ]
    pair_results: Annotated[
        tuple[MarketingJudgmentPairResult, ...], Field(min_length=1, max_length=3200)
    ]
    pass_count: Annotated[int, Field(ge=0, le=6400)]
    pair_pass_count: Annotated[int, Field(ge=0, le=3200)]
    all_trials_passed: bool

    @model_validator(mode="after")
    def require_aggregate_verdict(self) -> Self:
        if self.pass_count != sum(item.passed for item in self.results):
            raise ValueError("judgment canary pass count is inconsistent")
        if self.pair_pass_count != sum(item.passed for item in self.pair_results):
            raise ValueError("judgment canary pair pass count is inconsistent")
        expected = self.pass_count == len(self.results) and self.pair_pass_count == len(
            self.pair_results
        )
        if self.all_trials_passed != expected:
            raise ValueError("judgment canary aggregate verdict is inconsistent")
        return self


class MarketingJudgmentTrialRunner(Protocol):
    runtime_identity: MarketingJudgmentRuntimeIdentity

    def run(
        self,
        case: MarketingJudgmentCanaryInput,
        *,
        trial: int,
    ) -> MarketingJudgmentTrialObservation: ...


@dataclass(frozen=True, slots=True)
class HostedReassessmentTrialRunner:
    """Run each case in a fresh no-tool Codex workspace; grader data never enters the prompt."""

    codex: StructuredReassessmentJudgment
    output_root: Path
    runtime_identity: MarketingJudgmentRuntimeIdentity
    timeout_seconds: float = 240.0

    def run(
        self,
        case: MarketingJudgmentCanaryInput,
        *,
        trial: int,
    ) -> MarketingJudgmentTrialObservation:
        nonce = secrets.token_hex(32)
        nonce_sha256 = sha256(nonce.encode()).hexdigest()
        workspace_root = self.output_root / nonce_sha256
        task = _trial_task(case, nonce_sha256)
        executor = HostedOutcomeReassessmentExecutor(
            codex=self.codex,
            output_root=workspace_root,
            timeout_seconds=self.timeout_seconds,
        )
        prompt_sha256: str | None = None
        output_schema_sha256: str | None = None
        started = time.monotonic()
        try:
            prepared = executor.prepare(task)
            prompt_sha256 = sha256(prepared.prompt.encode()).hexdigest()
            output_schema_sha256 = _json_sha256(prepared.schema)
            result = executor.execute(prepared)
            reassessment = MarketingReassessment.model_validate(result.output["reassessment"])
        except (MarketingExecutionError, ValidationError) as error:
            return MarketingJudgmentTrialObservation(
                schema_version="trace.marketing-judgment-trial-observation.v1",
                case_id=case.case_id,
                trial=trial,
                trial_nonce_sha256=nonce_sha256,
                prompt_sha256=prompt_sha256,
                output_schema_sha256=output_schema_sha256,
                elapsed_milliseconds=_elapsed_milliseconds(started),
                state="failed",
                failure_code=_failure_code(error),
            )
        return MarketingJudgmentTrialObservation(
            schema_version="trace.marketing-judgment-trial-observation.v1",
            case_id=case.case_id,
            trial=trial,
            trial_nonce_sha256=nonce_sha256,
            prompt_sha256=prompt_sha256,
            output_schema_sha256=output_schema_sha256,
            elapsed_milliseconds=_elapsed_milliseconds(started),
            state="succeeded",
            reassessment=reassessment,
            reassessment_sha256=contract_sha256(reassessment),
        )


def evaluate_marketing_judgment_canary(
    cases: tuple[MarketingJudgmentCanaryCase, ...],
    runner: MarketingJudgmentTrialRunner,
    *,
    trials: int,
) -> MarketingJudgmentCanaryReport:
    """Run all predeclared trials; failures remain counted and paired grading is independent."""
    _validate_cases(cases)
    if not _MIN_TRIALS <= trials <= _MAX_TRIALS:
        raise MarketingJudgmentCanaryError("judgment_canary_trial_count_invalid")
    trial_results: list[MarketingJudgmentTrialResult] = []
    for trial in range(1, trials + 1):
        for case in cases:
            observation = runner.run(case.input, trial=trial)
            trial_results.append(_grade_trial(case, observation))
    _require_fresh_trials(trial_results)
    pair_results = _grade_pairs(cases, tuple(trial_results), trials=trials)
    results = tuple(trial_results)
    return MarketingJudgmentCanaryReport(
        schema_version="trace.marketing-judgment-canary-report.v1",
        corpus_sha256=marketing_judgment_canary_corpus_sha256(cases),
        runtime=runner.runtime_identity,
        trial_count=trials,
        results=results,
        pair_results=pair_results,
        pass_count=sum(item.passed for item in results),
        pair_pass_count=sum(item.passed for item in pair_results),
        all_trials_passed=all(item.passed for item in (*results, *pair_results)),
    )


def inspect_marketing_judgment_runtime(
    executable: Path,
    *,
    requested_model_id: str,
    provider_id: str = "openai-codex-cli",
) -> MarketingJudgmentRuntimeIdentity:
    """Inspect the requested Codex executable without claiming served-model attestation."""
    try:
        resolved = executable.resolve(strict=True)
        executable_sha256 = _file_sha256(resolved)
        completed = subprocess.run(  # noqa: S603
            [str(resolved), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise MarketingJudgmentCanaryError(
            "judgment_canary_runtime_identity_unavailable"
        ) from error
    reported_version = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not reported_version:
        raise MarketingJudgmentCanaryError("judgment_canary_runtime_identity_unavailable")
    try:
        package_version = version(_PACKAGE_NAME)
    except PackageNotFoundError as error:
        raise MarketingJudgmentCanaryError(
            "judgment_canary_package_identity_unavailable"
        ) from error
    return MarketingJudgmentRuntimeIdentity(
        schema_version="trace.marketing-judgment-runtime.v1",
        provider_id=provider_id,
        requested_model_id=requested_model_id,
        executable_name=resolved.name,
        executable_sha256=executable_sha256,
        executable_version=reported_version,
        package_version=package_version,
    )


def build_hosted_reassessment_trial_runner(
    executable: Path,
    *,
    model_id: str,
    output_root: Path,
    timeout_seconds: float = 240.0,
) -> HostedReassessmentTrialRunner:
    """Bind one concrete Codex executable/model to the runtime identity used in its report."""
    try:
        resolved = executable.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarketingJudgmentCanaryError(
            "judgment_canary_runtime_identity_unavailable"
        ) from error
    identity = inspect_marketing_judgment_runtime(resolved, requested_model_id=model_id)
    return HostedReassessmentTrialRunner(
        codex=CodexCli(executable=resolved, model=model_id),
        output_root=output_root,
        runtime_identity=identity,
        timeout_seconds=timeout_seconds,
    )


def marketing_judgment_canary_corpus_sha256(
    cases: tuple[MarketingJudgmentCanaryCase, ...],
) -> str:
    _validate_cases(cases)
    return _json_sha256(
        {
            "schema_version": "trace.marketing-judgment-canary-corpus.v1",
            "cases": [
                {
                    "input": case.input.model_dump(mode="json"),
                    "expectation": case.expectation.model_dump(mode="json"),
                }
                for case in cases
            ],
        }
    )


def _grade_trial(
    case: MarketingJudgmentCanaryCase,
    observation: MarketingJudgmentTrialObservation,
) -> MarketingJudgmentTrialResult:
    if observation.case_id != case.input.case_id:
        raise MarketingJudgmentCanaryError("judgment_canary_observation_identifier_mismatch")
    if observation.state == "failed" or observation.reassessment is None:
        failure = observation.failure_code or "judgment_canary_runner_failed"
        return MarketingJudgmentTrialResult(
            schema_version="trace.marketing-judgment-trial-result.v1",
            case_id=case.input.case_id,
            trial=observation.trial,
            process_passed=False,
            decision_quality=None,
            semantic_quality=None,
            passed=False,
            failure_codes=(failure,),
            observation=observation,
        )
    reassessment = observation.reassessment
    process_codes = _process_failure_codes(case.input.request, reassessment)
    decision_quality = evaluate_decision_quality(
        reassessment.decision_dossier,
        case.expectation.decision_scenario,
    )
    semantic_quality = _evaluate_semantics(reassessment, case.expectation)
    direction_codes = _direction_failure_codes(reassessment, case.expectation)
    failures = (
        *process_codes,
        *direction_codes,
        *(f"decision:{code}" for code in decision_quality.gap_codes),
        *(f"semantic:{item}" for item in semantic_quality.missing_anchor_ids),
        *(f"forbidden:{item}" for item in semantic_quality.forbidden_phrase_hits),
    )
    return MarketingJudgmentTrialResult(
        schema_version="trace.marketing-judgment-trial-result.v1",
        case_id=case.input.case_id,
        trial=observation.trial,
        process_passed=not process_codes,
        decision_quality=decision_quality,
        semantic_quality=semantic_quality,
        passed=not failures,
        failure_codes=failures,
        observation=observation,
    )


def _process_failure_codes(
    request: OutcomeReassessmentRequest,
    reassessment: MarketingReassessment,
) -> tuple[str, ...]:
    failures: list[str] = []
    if (
        reassessment.campaign_id != request.campaign_id
        or reassessment.trigger_evaluation_id != request.evaluation.evaluation_id
        or reassessment.trigger_evaluation_sha256 != request.evaluation_sha256
        or reassessment.situation != request.situation
    ):
        failures.append("reassessment_source_binding_invalid")
    if {item.hypothesis_id for item in reassessment.hypothesis_reassessments} != {
        item.hypothesis_id for item in request.prior_strategy.hypotheses
    }:
        failures.append("reassessment_hypothesis_coverage_invalid")
    return tuple(failures)


def _evaluate_semantics(
    reassessment: MarketingReassessment,
    expectation: MarketingJudgmentCanaryExpectation,
) -> MarketingJudgmentSemanticEvaluation:
    fields = _semantic_fields(reassessment)
    matched: list[str] = []
    missing: list[str] = []
    for anchor in expectation.semantic_anchors:
        haystack = _normalize_text(fields[anchor.field])
        if any(_normalize_text(concept) in haystack for concept in anchor.any_of):
            matched.append(anchor.anchor_id)
        else:
            missing.append(anchor.anchor_id)
    combined = _normalize_text(" ".join(fields.values()))
    forbidden_hits = tuple(
        phrase for phrase in expectation.forbidden_phrases if _normalize_text(phrase) in combined
    )
    return MarketingJudgmentSemanticEvaluation(
        schema_version="trace.marketing-judgment-semantic-evaluation.v1",
        passed=not missing and not forbidden_hits,
        matched_anchor_ids=tuple(matched),
        missing_anchor_ids=tuple(missing),
        forbidden_phrase_hits=forbidden_hits,
    )


def _semantic_fields(reassessment: MarketingReassessment) -> dict[SemanticField, str]:
    return {
        "decision_reason": reassessment.decision_dossier.reason,
        "unanswered_questions": " ".join(reassessment.unanswered_questions),
        "hypothesis_rationales": " ".join(
            item.rationale for item in reassessment.hypothesis_reassessments
        ),
        "next_tests": " ".join(
            item.next_test for item in reassessment.hypothesis_reassessments if item.next_test
        ),
    }


def _direction_failure_codes(
    reassessment: MarketingReassessment,
    expectation: MarketingJudgmentCanaryExpectation,
) -> tuple[str, ...]:
    failures: list[str] = []
    evidence = {
        item.evidence_id: item for item in reassessment.decision_dossier.evidence_dispositions
    }
    for expected in expectation.evidence_directions:
        observed = evidence.get(expected.evidence_id)
        if (
            observed is None
            or observed.disposition != expected.disposition
            or observed.use != expected.use
        ):
            failures.append(f"evidence_direction:{expected.evidence_id}")
    hypotheses = {item.hypothesis_id: item for item in reassessment.hypothesis_reassessments}
    for expected in expectation.hypothesis_directions:
        observed = hypotheses.get(expected.hypothesis_id)
        if (
            observed is None
            or observed.disposition != expected.disposition
            or (observed.next_test is not None) != expected.next_test_required
        ):
            failures.append(f"hypothesis_direction:{expected.hypothesis_id}")
    return tuple(failures)


def _grade_pairs(
    cases: tuple[MarketingJudgmentCanaryCase, ...],
    results: tuple[MarketingJudgmentTrialResult, ...],
    *,
    trials: int,
) -> tuple[MarketingJudgmentPairResult, ...]:
    cases_by_pair: dict[str, list[MarketingJudgmentCanaryCase]] = {}
    for case in cases:
        cases_by_pair.setdefault(case.expectation.counterfactual_pair_id, []).append(case)
    output: list[MarketingJudgmentPairResult] = []
    indexed = {(item.case_id, item.trial): item for item in results}
    for pair_id, paired in sorted(cases_by_pair.items()):
        if len(paired) != _PAIR_CARDINALITY:
            raise MarketingJudgmentCanaryError("judgment_canary_pair_cardinality_invalid")
        required = paired[0].expectation.required_pair_differences
        if paired[1].expectation.required_pair_differences != required:
            raise MarketingJudgmentCanaryError("judgment_canary_pair_contract_mismatch")
        case_ids = cast(
            "tuple[AgentIdentifier, AgentIdentifier]",
            tuple(item.input.case_id for item in paired),
        )
        for trial in range(1, trials + 1):
            left = indexed[(case_ids[0], trial)]
            right = indexed[(case_ids[1], trial)]
            observed = _pair_differences(left.observation, right.observation)
            missing = tuple(item for item in required if item not in observed)
            failures = (
                ("counterfactual_trial_failed",)
                if left.observation.state != "succeeded" or right.observation.state != "succeeded"
                else tuple(f"missing_difference:{item}" for item in missing)
            )
            output.append(
                MarketingJudgmentPairResult(
                    schema_version="trace.marketing-judgment-pair-result.v1",
                    pair_id=pair_id,
                    trial=trial,
                    case_ids=case_ids,
                    required_differences=required,
                    observed_differences=observed,
                    passed=not failures,
                    failure_codes=failures,
                )
            )
    return tuple(output)


def _pair_differences(
    left: MarketingJudgmentTrialObservation,
    right: MarketingJudgmentTrialObservation,
) -> tuple[PairDifference, ...]:
    if left.reassessment is None or right.reassessment is None:
        return ()
    left_dossier = left.reassessment.decision_dossier
    right_dossier = right.reassessment.decision_dossier
    comparisons: tuple[tuple[PairDifference, object, object], ...] = (
        (
            "recommended_next_step",
            left_dossier.recommended_next_step,
            right_dossier.recommended_next_step,
        ),
        (
            "hypothesis_dispositions",
            tuple(
                (item.hypothesis_id, item.disposition)
                for item in sorted(
                    left.reassessment.hypothesis_reassessments,
                    key=lambda item: item.hypothesis_id,
                )
            ),
            tuple(
                (item.hypothesis_id, item.disposition)
                for item in sorted(
                    right.reassessment.hypothesis_reassessments,
                    key=lambda item: item.hypothesis_id,
                )
            ),
        ),
        (
            "evidence_dispositions",
            tuple(
                (item.evidence_id, item.disposition, item.use)
                for item in sorted(
                    left_dossier.evidence_dispositions,
                    key=lambda item: item.evidence_id,
                )
            ),
            tuple(
                (item.evidence_id, item.disposition, item.use)
                for item in sorted(
                    right_dossier.evidence_dispositions,
                    key=lambda item: item.evidence_id,
                )
            ),
        ),
        (
            "unanswered_questions",
            tuple(sorted(_normalize_text(item) for item in left.reassessment.unanswered_questions)),
            tuple(
                sorted(_normalize_text(item) for item in right.reassessment.unanswered_questions)
            ),
        ),
    )
    return tuple(name for name, first, second in comparisons if first != second)


def _validate_cases(cases: tuple[MarketingJudgmentCanaryCase, ...]) -> None:
    if not _PAIR_CARDINALITY <= len(cases) <= _MAX_CASES:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_size_invalid")
    case_ids = tuple(item.input.case_id for item in cases)
    if len(set(case_ids)) != len(case_ids):
        raise MarketingJudgmentCanaryError("judgment_canary_duplicate_case")
    pairs: dict[str, list[MarketingJudgmentCanaryCase]] = {}
    for case in cases:
        pair_id = case.expectation.counterfactual_pair_id
        pairs.setdefault(pair_id, []).append(case)
    if any(len(paired) != _PAIR_CARDINALITY for paired in pairs.values()):
        raise MarketingJudgmentCanaryError("judgment_canary_pair_cardinality_invalid")
    for paired in pairs.values():
        _require_controlled_counterfactual_pair(paired[0], paired[1])


def _require_controlled_counterfactual_pair(
    left: MarketingJudgmentCanaryCase,
    right: MarketingJudgmentCanaryCase,
) -> None:
    left_request = left.input.request
    right_request = right.input.request
    if _counterfactual_context(left_request) != _counterfactual_context(right_request):
        raise MarketingJudgmentCanaryError("judgment_canary_pair_context_mismatch")
    if left_request.evaluation_sha256 == right_request.evaluation_sha256:
        raise MarketingJudgmentCanaryError("judgment_canary_pair_evidence_not_perturbed")


def _counterfactual_context(request: OutcomeReassessmentRequest) -> tuple[object, ...]:
    evaluation = request.evaluation
    return (
        request.account_id,
        request.campaign_id,
        request.situation,
        request.prior_strategy_sha256,
        request.supported_claim_ids,
        request.requested_by,
        evaluation.evaluation_id,
        evaluation.campaign_id,
        evaluation.experiment_id,
        evaluation.outcome_scope,
        evaluation.evaluated_at,
        evaluation.lineage_ids,
    )


def _require_fresh_trials(results: list[MarketingJudgmentTrialResult]) -> None:
    nonces = tuple(item.observation.trial_nonce_sha256 for item in results)
    if len(set(nonces)) != len(nonces):
        raise MarketingJudgmentCanaryError("judgment_canary_trial_reused")


def _trial_task(case: MarketingJudgmentCanaryInput, nonce_sha256: str) -> MarketingTask:
    request = case.request
    return MarketingTask(
        task_id=f"canary-{nonce_sha256[:32]}",
        run_id=f"canary-run-{nonce_sha256[:32]}",
        account_id=request.account_id,
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key=f"judgment-canary:{nonce_sha256}",
        payload=cast("JsonObject", request.model_dump(mode="json")),
        created_at=request.evaluation.evaluated_at,
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise MarketingJudgmentCanaryError(
            "judgment_canary_runtime_identity_unavailable"
        ) from error
    return digest.hexdigest()


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(encoded.encode()).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _elapsed_milliseconds(started: float) -> int:
    return min(round((time.monotonic() - started) * 1000), 3_600_000)


def _failure_code(error: Exception) -> str:
    if isinstance(error, MarketingExecutionError):
        return error.failure_code
    if isinstance(error, ValidationError):
        return "judgment_canary_result_contract_invalid"
    return "judgment_canary_runner_failed"
