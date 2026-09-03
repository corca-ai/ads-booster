"""Comparative provider canary for adaptive marketing evidence research.

This module evaluates whether the installed dynamic research composition changes its evidence plan
when one governed customer fact changes.  It keeps process validity separate from marketing quality
and compares the Trace planner with a same-runtime baseline.  Source tests prove the evaluator, not
provider quality; superiority remains false without a sufficiently diverse private corpus and blind
human preferences.
"""

from __future__ import annotations

import json
import math
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self, cast

from pydantic import Field, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import AgentIdentifier, contract_sha256
from ads_booster.contracts.marketing_context import MarketingContextPlanningProjection
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchError,
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchRunner,
    DynamicResearchCodex,
    build_dynamic_research_registry,
    planner_protocol_sha256,
)
from ads_booster.marketing.evidence_research_operator import ResearchScope
from ads_booster.marketing.marketing_judgment_canary import (
    inspect_marketing_judgment_runtime,
)
from ads_booster.providers.codex_cli import CodexCli, CodexCliError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

_MIN_TRIALS = 2
_MAX_TRIALS = 100
_PAIR_SIZE = 2
_MAX_CASES = 64
_MIN_SUPERIORITY_PAIRS = 20
_MIN_RATERS_PER_PAIR = 3
_Z_95 = 1.959963984540054
_PREFERENCE_NEUTRAL = 0.5
_QUESTION_SIMILARITY_CEILING = 0.92

PlanDifference = Literal[
    "scope_order",
    "claim_focus",
    "research_questions",
    "counter_evidence_questions",
]
SemanticField = Literal["research_questions", "counter_evidence_questions"]
PreferenceWinner = Literal["candidate", "baseline", "tie"]

_ACTION_BY_SCOPE: dict[ResearchScope, str] = {
    ResearchScope.PRODUCT_TRUTH: "observe.product_truth",
    ResearchScope.CUSTOMER_INTELLIGENCE: "observe.customer_intelligence",
    ResearchScope.MARKET_EVIDENCE: "observe.market_evidence",
}


class DynamicResearchCanaryError(ValueError):
    """The comparative corpus, runner, or report violates its proof contract."""


class DynamicResearchCanaryInput(ContractModel):
    """Complete input visible to either evaluated runner; grader material is separate."""

    schema_version: Literal["trace.dynamic-research-canary-input.v1"]
    case_id: AgentIdentifier
    request: DynamicEvidenceResearchRequest


class DynamicResearchSemanticAnchor(ContractModel):
    anchor_id: AgentIdentifier
    field: SemanticField
    any_of: Annotated[tuple[str, ...], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def require_distinct_concepts(self) -> Self:
        concepts = tuple(_normalize_text(item) for item in self.any_of)
        if any(not item for item in concepts) or len(set(concepts)) != len(concepts):
            raise ValueError("dynamic research anchor concepts must be unique and nonempty")
        return self


class DynamicResearchCanaryExpectation(ContractModel):
    """Private direction and semantics never supplied to an evaluated runner."""

    schema_version: Literal["trace.dynamic-research-canary-expectation.v1"]
    case_id: AgentIdentifier
    expected_terminal_state: Literal["completed", "inconclusive", "awaiting_reconciliation"]
    semantic_anchors: Annotated[
        tuple[DynamicResearchSemanticAnchor, ...], Field(min_length=1, max_length=12)
    ]
    forbidden_phrases: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    counterfactual_pair_id: AgentIdentifier
    perturbed_signal_id: AgentIdentifier
    required_pair_differences: Annotated[
        tuple[PlanDifference, ...], Field(min_length=1, max_length=4)
    ]

    @model_validator(mode="after")
    def require_private_contract(self) -> Self:
        anchor_ids = tuple(item.anchor_id for item in self.semantic_anchors)
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("dynamic research anchor IDs must be unique")
        if len(set(self.required_pair_differences)) != len(self.required_pair_differences):
            raise ValueError("dynamic research pair differences must be unique")
        forbidden = tuple(_normalize_text(item) for item in self.forbidden_phrases)
        if any(not item for item in forbidden) or len(set(forbidden)) != len(forbidden):
            raise ValueError("dynamic research forbidden phrases must be unique and nonempty")
        return self


@dataclass(frozen=True, slots=True)
class DynamicResearchCanaryCase:
    input: DynamicResearchCanaryInput
    expectation: DynamicResearchCanaryExpectation

    def __post_init__(self) -> None:
        """Reject an input/private-expectation pair with different opaque IDs."""
        if self.input.case_id != self.expectation.case_id:
            raise DynamicResearchCanaryError("dynamic_research_case_identifier_mismatch")


class DynamicResearchRuntimeIdentity(ContractModel):
    """Observed executable/package plus comparison-critical environment identity."""

    schema_version: Literal["trace.dynamic-research-runtime.v1"]
    runner_id: AgentIdentifier
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    requested_model_id: Annotated[str, Field(min_length=1, max_length=240)]
    executable_name: Annotated[str, Field(min_length=1, max_length=255)]
    executable_sha256: Sha256Digest
    executable_version: Annotated[str, Field(min_length=1, max_length=500)]
    package_version: Annotated[str, Field(min_length=1, max_length=120)]
    planner_protocol_sha256: Sha256Digest
    tool_environment_sha256: Sha256Digest


class DynamicResearchPlanStep(ContractModel):
    iteration: Annotated[int, Field(ge=1, le=6)]
    action_id: Literal[
        "observe.product_truth",
        "observe.customer_intelligence",
        "observe.market_evidence",
    ]
    scope: ResearchScope
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]
    research_question: Annotated[str, Field(min_length=1, max_length=1000)]
    counter_evidence_question: Annotated[str, Field(min_length=1, max_length=1000)]
    prompt_sha256: Sha256Digest
    output_schema_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_bound_action(self) -> Self:
        if self.action_id != _ACTION_BY_SCOPE[self.scope]:
            raise ValueError("dynamic research plan action and scope differ")
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("dynamic research plan claim IDs must be unique")
        return self


class DynamicResearchRunProof(ContractModel):
    input_snapshot_sha256: Sha256Digest
    registry_snapshot_sha256: Sha256Digest
    planner_protocol_sha256: Sha256Digest
    trace_sha256: Sha256Digest
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    terminal_state: Literal["completed", "inconclusive", "awaiting_reconciliation"]
    tool_calls: Annotated[int, Field(ge=0, le=6)]
    spent_cost_units: Annotated[int, Field(ge=0, le=24)]


class DynamicResearchTrialObservation(ContractModel):
    schema_version: Literal["trace.dynamic-research-trial-observation.v1"]
    runner_id: AgentIdentifier
    case_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    trial_nonce_sha256: Sha256Digest
    source_input_sha256: Sha256Digest
    effective_input_sha256: Sha256Digest
    elapsed_milliseconds: Annotated[int, Field(ge=0, le=3_600_000)]
    state: Literal["succeeded", "failed"]
    failure_code: Annotated[str | None, Field(max_length=300)] = None
    proof: DynamicResearchRunProof | None = None
    plan_steps: Annotated[tuple[DynamicResearchPlanStep, ...], Field(max_length=6)] = ()

    @model_validator(mode="after")
    def require_terminal_shape(self) -> Self:
        if self.state == "succeeded":
            if self.failure_code is not None or self.proof is None:
                raise ValueError("successful dynamic research trial requires proof")
        elif self.failure_code is None or self.proof is not None or self.plan_steps:
            raise ValueError("failed dynamic research trial requires only a failure code")
        return self


class DynamicResearchTrialResult(ContractModel):
    schema_version: Literal["trace.dynamic-research-trial-result.v1"]
    runner_id: AgentIdentifier
    case_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    process_valid: bool
    marketing_quality_passed: bool
    matched_anchor_ids: tuple[AgentIdentifier, ...] = ()
    missing_anchor_ids: tuple[AgentIdentifier, ...] = ()
    failure_codes: tuple[str, ...] = ()
    observation: DynamicResearchTrialObservation


class DynamicResearchPairResult(ContractModel):
    schema_version: Literal["trace.dynamic-research-pair-result.v1"]
    runner_id: AgentIdentifier
    pair_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    case_ids: Annotated[tuple[AgentIdentifier, AgentIdentifier], Field(min_length=2, max_length=2)]
    required_differences: tuple[PlanDifference, ...]
    observed_differences: tuple[PlanDifference, ...]
    passed: bool
    failure_codes: tuple[str, ...] = ()


class DynamicResearchBlindPreference(ContractModel):
    """Identity-blinded human comparison, supplied after both outputs are rendered."""

    pair_id: AgentIdentifier
    trial: Annotated[int, Field(ge=1, le=100)]
    rater_id: AgentIdentifier
    winner: PreferenceWinner


class DynamicResearchCanaryReport(ContractModel):
    schema_version: Literal["trace.dynamic-research-canary-report.v1"]
    corpus_sha256: Sha256Digest
    candidate_runtime: DynamicResearchRuntimeIdentity
    baseline_runtime: DynamicResearchRuntimeIdentity
    trial_count: Annotated[int, Field(ge=2, le=100)]
    candidate_results: tuple[DynamicResearchTrialResult, ...]
    baseline_results: tuple[DynamicResearchTrialResult, ...]
    candidate_pair_results: tuple[DynamicResearchPairResult, ...]
    baseline_pair_results: tuple[DynamicResearchPairResult, ...]
    candidate_process_valid: bool
    baseline_process_valid: bool
    candidate_noninferior: bool
    blind_candidate_preference_rate: float | None = None
    blind_preference_lower_bound_95: float | None = None
    superiority_claim_allowed: bool

    @model_validator(mode="after")
    def require_honest_superiority(self) -> Self:
        if self.superiority_claim_allowed and (
            not self.candidate_process_valid
            or not self.baseline_process_valid
            or not self.candidate_noninferior
            or self.blind_preference_lower_bound_95 is None
            or self.blind_preference_lower_bound_95 <= _PREFERENCE_NEUTRAL
            or len({item.pair_id for item in self.candidate_pair_results}) < _MIN_SUPERIORITY_PAIRS
        ):
            raise ValueError("dynamic research superiority lacks sufficient evidence")
        return self


class DynamicResearchTrialRunner(Protocol):
    @property
    def runtime_identity(self) -> DynamicResearchRuntimeIdentity: ...

    def run(
        self,
        case: DynamicResearchCanaryInput,
        *,
        trial: int,
    ) -> DynamicResearchTrialObservation: ...


class DynamicResearchTrialConfiguration(ContractModel):
    runner_id: AgentIdentifier
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    tool_environment_sha256: Sha256Digest
    timeout_seconds: Annotated[float, Field(ge=30.0, le=1800.0)] = 300.0


@dataclass(slots=True)
class _RecordingCodex:
    delegate: DynamicResearchCodex
    steps: list[tuple[str, JsonObject, JsonObject]] = field(default_factory=list)

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        result = self.delegate.run_marketing_judgment_job(
            prompt,
            schema,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )
        self.steps.append((prompt, schema, result))
        return result

    def run_marketing_research_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        return self.delegate.run_marketing_research_job(
            prompt,
            schema,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class DynamicResearchProviderTrialRunner:
    """Run the existing production composition in a fresh, non-linkable state root per trial."""

    codex: DynamicResearchCodex
    output_root: Path
    runtime_identity: DynamicResearchRuntimeIdentity
    timeout_seconds: float = 300.0

    def run(
        self,
        case: DynamicResearchCanaryInput,
        *,
        trial: int,
    ) -> DynamicResearchTrialObservation:
        nonce = secrets.token_hex(32)
        nonce_sha256 = sha256(nonce.encode()).hexdigest()
        started = time.monotonic()
        recorder = _RecordingCodex(self.codex)
        request = case.request.model_copy(update={"session_id": f"canary-{nonce_sha256[:32]}"})
        try:
            result = DynamicEvidenceResearchRunner(
                codex=recorder,
                state_root=self.output_root / nonce_sha256,
                provider_id=self.runtime_identity.provider_id,
                model_id=self.runtime_identity.requested_model_id,
                timeout_seconds=self.timeout_seconds,
            ).run(request)
            steps = tuple(
                _plan_step(index, prompt, schema, output)
                for index, (prompt, schema, output) in enumerate(recorder.steps, start=1)
            )
        except (CodexCliError, DynamicEvidenceResearchError, ValidationError) as error:
            return DynamicResearchTrialObservation(
                schema_version="trace.dynamic-research-trial-observation.v1",
                runner_id=self.runtime_identity.runner_id,
                case_id=case.case_id,
                trial=trial,
                trial_nonce_sha256=nonce_sha256,
                source_input_sha256=contract_sha256(case.request),
                effective_input_sha256=contract_sha256(request),
                elapsed_milliseconds=_elapsed_milliseconds(started),
                state="failed",
                failure_code=_failure_code(error),
            )
        return DynamicResearchTrialObservation(
            schema_version="trace.dynamic-research-trial-observation.v1",
            runner_id=self.runtime_identity.runner_id,
            case_id=case.case_id,
            trial=trial,
            trial_nonce_sha256=nonce_sha256,
            source_input_sha256=contract_sha256(case.request),
            effective_input_sha256=contract_sha256(request),
            elapsed_milliseconds=_elapsed_milliseconds(started),
            state="succeeded",
            proof=DynamicResearchRunProof(
                input_snapshot_sha256=result.input_snapshot_sha256,
                registry_snapshot_sha256=result.registry_snapshot_sha256,
                planner_protocol_sha256=result.planner_protocol_sha256,
                trace_sha256=result.trace_sha256,
                provider_id=result.provider_id,
                model_id=result.model_id,
                terminal_state=result.state,
                tool_calls=result.tool_calls,
                spent_cost_units=result.spent_cost_units,
            ),
            plan_steps=steps,
        )


def build_dynamic_research_trial_runner(
    executable: Path,
    output_root: Path,
    configuration: DynamicResearchTrialConfiguration,
) -> DynamicResearchProviderTrialRunner:
    observed = inspect_marketing_judgment_runtime(
        executable,
        requested_model_id=configuration.model_id,
    )
    identity = DynamicResearchRuntimeIdentity(
        schema_version="trace.dynamic-research-runtime.v1",
        runner_id=configuration.runner_id,
        provider_id=observed.provider_id,
        requested_model_id=observed.requested_model_id,
        executable_name=observed.executable_name,
        executable_sha256=observed.executable_sha256,
        executable_version=observed.executable_version,
        package_version=observed.package_version,
        planner_protocol_sha256=planner_protocol_sha256(),
        tool_environment_sha256=configuration.tool_environment_sha256,
    )
    return DynamicResearchProviderTrialRunner(
        codex=CodexCli(executable=executable.resolve(), model=configuration.model_id),
        output_root=output_root,
        runtime_identity=identity,
        timeout_seconds=configuration.timeout_seconds,
    )


def evaluate_dynamic_research_canary(
    cases: tuple[DynamicResearchCanaryCase, ...],
    candidate: DynamicResearchTrialRunner,
    baseline: DynamicResearchTrialRunner,
    *,
    trials: int,
    blind_preferences: tuple[DynamicResearchBlindPreference, ...] = (),
) -> DynamicResearchCanaryReport:
    """Compare paired adaptation without turning source tests into a superiority claim."""
    _validate_cases(cases)
    _require_comparable_runtimes(candidate.runtime_identity, baseline.runtime_identity)
    if not _MIN_TRIALS <= trials <= _MAX_TRIALS:
        raise DynamicResearchCanaryError("dynamic_research_trial_count_invalid")
    candidate_results = _run_and_grade(cases, candidate, trials=trials)
    baseline_results = _run_and_grade(cases, baseline, trials=trials)
    _require_fresh_observations((*candidate_results, *baseline_results))
    candidate_pairs = _grade_pairs(cases, candidate_results, trials=trials)
    baseline_pairs = _grade_pairs(cases, baseline_results, trials=trials)
    candidate_valid = all(item.process_valid for item in candidate_results)
    baseline_valid = all(item.process_valid for item in baseline_results)
    candidate_pair_passes = sum(item.passed for item in candidate_pairs)
    baseline_pair_passes = sum(item.passed for item in baseline_pairs)
    candidate_quality = sum(item.marketing_quality_passed for item in candidate_results)
    baseline_quality = sum(item.marketing_quality_passed for item in baseline_results)
    noninferior = bool(
        candidate_valid
        and baseline_valid
        and all(item.marketing_quality_passed for item in candidate_results)
        and all(item.passed for item in candidate_pairs)
        and candidate_pair_passes >= baseline_pair_passes
        and candidate_quality >= baseline_quality
    )
    rate, lower, preferences_valid = _blind_preference_statistics(
        cases,
        trials=trials,
        preferences=blind_preferences,
    )
    pair_count = len({item.expectation.counterfactual_pair_id for item in cases})
    superiority = bool(
        noninferior
        and preferences_valid
        and pair_count >= _MIN_SUPERIORITY_PAIRS
        and lower is not None
        and lower > _PREFERENCE_NEUTRAL
    )
    return DynamicResearchCanaryReport(
        schema_version="trace.dynamic-research-canary-report.v1",
        corpus_sha256=dynamic_research_canary_corpus_sha256(cases),
        candidate_runtime=candidate.runtime_identity,
        baseline_runtime=baseline.runtime_identity,
        trial_count=trials,
        candidate_results=candidate_results,
        baseline_results=baseline_results,
        candidate_pair_results=candidate_pairs,
        baseline_pair_results=baseline_pairs,
        candidate_process_valid=candidate_valid,
        baseline_process_valid=baseline_valid,
        candidate_noninferior=noninferior,
        blind_candidate_preference_rate=rate,
        blind_preference_lower_bound_95=lower,
        superiority_claim_allowed=superiority,
    )


def dynamic_research_canary_corpus_sha256(
    cases: tuple[DynamicResearchCanaryCase, ...],
) -> str:
    _validate_cases(cases)
    return _json_sha256(
        {
            "schema_version": "trace.dynamic-research-canary-corpus.v1",
            "cases": [
                {
                    "input": item.input.model_dump(mode="json"),
                    "expectation": item.expectation.model_dump(mode="json"),
                }
                for item in cases
            ],
        }
    )


def _run_and_grade(
    cases: tuple[DynamicResearchCanaryCase, ...],
    runner: DynamicResearchTrialRunner,
    *,
    trials: int,
) -> tuple[DynamicResearchTrialResult, ...]:
    results: list[DynamicResearchTrialResult] = []
    for trial in range(1, trials + 1):
        for case in cases:
            observation = runner.run(case.input, trial=trial)
            results.append(_grade_trial(case, runner.runtime_identity, observation))
    return tuple(results)


def _grade_trial(
    case: DynamicResearchCanaryCase,
    runtime: DynamicResearchRuntimeIdentity,
    observation: DynamicResearchTrialObservation,
) -> DynamicResearchTrialResult:
    failures: list[str] = []
    if observation.runner_id != runtime.runner_id or observation.case_id != case.input.case_id:
        raise DynamicResearchCanaryError("dynamic_research_observation_identity_mismatch")
    proof = observation.proof
    if observation.state == "failed" or proof is None:
        failures.append(observation.failure_code or "dynamic_research_runner_failed")
    else:
        failures.extend(_proof_failure_codes(case, runtime, observation))
    fields = _semantic_fields(observation.plan_steps)
    matched: list[str] = []
    missing: list[str] = []
    for anchor in case.expectation.semantic_anchors:
        haystack = fields[anchor.field]
        if any(_normalize_text(concept) in haystack for concept in anchor.any_of):
            matched.append(anchor.anchor_id)
        else:
            missing.append(anchor.anchor_id)
    combined = " ".join(fields.values())
    forbidden = tuple(
        phrase
        for phrase in case.expectation.forbidden_phrases
        if _normalize_text(phrase) in combined
    )
    failures.extend(f"semantic:{item}" for item in missing)
    failures.extend(f"forbidden:{item}" for item in forbidden)
    process_valid = not any(not item.startswith(("semantic:", "forbidden:")) for item in failures)
    marketing_quality = not missing and not forbidden and process_valid
    return DynamicResearchTrialResult(
        schema_version="trace.dynamic-research-trial-result.v1",
        runner_id=runtime.runner_id,
        case_id=case.input.case_id,
        trial=observation.trial,
        process_valid=process_valid,
        marketing_quality_passed=marketing_quality,
        matched_anchor_ids=tuple(matched),
        missing_anchor_ids=tuple(missing),
        failure_codes=tuple(failures),
        observation=observation,
    )


def _proof_failure_codes(
    case: DynamicResearchCanaryCase,
    runtime: DynamicResearchRuntimeIdentity,
    observation: DynamicResearchTrialObservation,
) -> list[str]:
    proof = observation.proof
    if proof is None:
        raise DynamicResearchCanaryError("dynamic_research_proof_missing")
    failures: list[str] = []
    checks = (
        (
            observation.source_input_sha256 == contract_sha256(case.input.request),
            "dynamic_research_source_input_binding_invalid",
        ),
        (
            proof.input_snapshot_sha256 == observation.effective_input_sha256,
            "dynamic_research_effective_input_binding_invalid",
        ),
        (
            proof.provider_id == runtime.provider_id
            and proof.model_id == runtime.requested_model_id,
            "dynamic_research_runtime_binding_invalid",
        ),
        (
            proof.planner_protocol_sha256 == runtime.planner_protocol_sha256,
            "dynamic_research_planner_protocol_invalid",
        ),
        (
            proof.registry_snapshot_sha256
            == build_dynamic_research_registry(case.input.request.required_scopes).snapshot_sha256,
            "dynamic_research_registry_binding_invalid",
        ),
        (
            proof.terminal_state == case.expectation.expected_terminal_state,
            "dynamic_research_terminal_state_unexpected",
        ),
        (
            proof.tool_calls == len(observation.plan_steps),
            "dynamic_research_plan_trace_incomplete",
        ),
        (
            tuple(step.iteration for step in observation.plan_steps)
            == tuple(range(1, len(observation.plan_steps) + 1)),
            "dynamic_research_plan_iteration_invalid",
        ),
    )
    failures.extend(code for passed, code in checks if not passed)
    return failures


def _grade_pairs(
    cases: tuple[DynamicResearchCanaryCase, ...],
    results: tuple[DynamicResearchTrialResult, ...],
    *,
    trials: int,
) -> tuple[DynamicResearchPairResult, ...]:
    grouped: dict[str, list[DynamicResearchCanaryCase]] = {}
    for case in cases:
        grouped.setdefault(case.expectation.counterfactual_pair_id, []).append(case)
    indexed = {(item.case_id, item.trial): item for item in results}
    runner_id = results[0].runner_id
    output: list[DynamicResearchPairResult] = []
    for pair_id, paired in sorted(grouped.items()):
        case_ids = cast(
            "tuple[AgentIdentifier, AgentIdentifier]",
            tuple(item.input.case_id for item in paired),
        )
        required = paired[0].expectation.required_pair_differences
        for trial in range(1, trials + 1):
            left = indexed[(case_ids[0], trial)]
            right = indexed[(case_ids[1], trial)]
            observed = _plan_differences(
                left.observation.plan_steps,
                right.observation.plan_steps,
            )
            missing = tuple(item for item in required if item not in observed)
            failures = (
                ("counterfactual_trial_invalid",)
                if not left.process_valid or not right.process_valid
                else tuple(f"missing_difference:{item}" for item in missing)
            )
            output.append(
                DynamicResearchPairResult(
                    schema_version="trace.dynamic-research-pair-result.v1",
                    runner_id=runner_id,
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


def _plan_differences(
    left: tuple[DynamicResearchPlanStep, ...],
    right: tuple[DynamicResearchPlanStep, ...],
) -> tuple[PlanDifference, ...]:
    comparisons: tuple[tuple[PlanDifference, object, object], ...] = (
        ("scope_order", tuple(item.scope for item in left), tuple(item.scope for item in right)),
        (
            "claim_focus",
            tuple(item.claim_ids for item in left),
            tuple(item.claim_ids for item in right),
        ),
    )
    differences: list[PlanDifference] = [
        name for name, first, second in comparisons if first != second
    ]
    if _question_sequences_differ(left, right, field="research_question"):
        differences.append("research_questions")
    if _question_sequences_differ(left, right, field="counter_evidence_question"):
        differences.append("counter_evidence_questions")
    return tuple(differences)


def _question_sequences_differ(
    left: tuple[DynamicResearchPlanStep, ...],
    right: tuple[DynamicResearchPlanStep, ...],
    *,
    field: Literal["research_question", "counter_evidence_question"],
) -> bool:
    first_values = (
        (item.research_question for item in left)
        if field == "research_question"
        else (item.counter_evidence_question for item in left)
    )
    second_values = (
        (item.research_question for item in right)
        if field == "research_question"
        else (item.counter_evidence_question for item in right)
    )
    first = " ".join(_normalize_text(value) for value in first_values)
    second = " ".join(_normalize_text(value) for value in second_values)
    return bool(
        first != second
        and SequenceMatcher(None, first, second).ratio() < _QUESTION_SIMILARITY_CEILING
    )


def _validate_cases(cases: tuple[DynamicResearchCanaryCase, ...]) -> None:
    if not _PAIR_SIZE <= len(cases) <= _MAX_CASES:
        raise DynamicResearchCanaryError("dynamic_research_corpus_size_invalid")
    if len({item.input.case_id for item in cases}) != len(cases):
        raise DynamicResearchCanaryError("dynamic_research_duplicate_case")
    grouped: dict[str, list[DynamicResearchCanaryCase]] = {}
    for case in cases:
        grouped.setdefault(case.expectation.counterfactual_pair_id, []).append(case)
    if any(len(items) != _PAIR_SIZE for items in grouped.values()):
        raise DynamicResearchCanaryError("dynamic_research_pair_cardinality_invalid")
    for paired in grouped.values():
        _require_controlled_signal_pair(paired[0], paired[1])


def _require_controlled_signal_pair(
    left: DynamicResearchCanaryCase,
    right: DynamicResearchCanaryCase,
) -> None:
    left_expectation = left.expectation
    right_expectation = right.expectation
    if (
        left_expectation.perturbed_signal_id != right_expectation.perturbed_signal_id
        or left_expectation.required_pair_differences != right_expectation.required_pair_differences
    ):
        raise DynamicResearchCanaryError("dynamic_research_pair_contract_mismatch")
    left_request = left.input.request
    right_request = right.input.request
    left_context = left_request.marketing_context
    right_context = right_request.marketing_context
    if left_context is None or right_context is None:
        raise DynamicResearchCanaryError("dynamic_research_pair_signal_missing")
    if _request_context_without_marketing(left_request) != _request_context_without_marketing(
        right_request
    ):
        raise DynamicResearchCanaryError("dynamic_research_pair_context_mismatch")
    signal_id = left_expectation.perturbed_signal_id
    left_signals = {item.signal_id: item for item in left_context.customer_signals}
    right_signals = {item.signal_id: item for item in right_context.customer_signals}
    if set(left_signals) != set(right_signals) or signal_id not in left_signals:
        raise DynamicResearchCanaryError("dynamic_research_pair_signal_missing")
    if _context_without_perturbation(left_context, signal_id) != _context_without_perturbation(
        right_context,
        signal_id,
    ):
        raise DynamicResearchCanaryError("dynamic_research_pair_context_mismatch")
    left_signal = left_signals[signal_id]
    right_signal = right_signals[signal_id]
    if (
        left_signal.summary == right_signal.summary
        or left_signal.signal_sha256 == right_signal.signal_sha256
    ):
        raise DynamicResearchCanaryError("dynamic_research_pair_signal_not_perturbed")
    if left_signal.model_dump(exclude={"summary", "signal_sha256"}) != right_signal.model_dump(
        exclude={"summary", "signal_sha256"}
    ):
        raise DynamicResearchCanaryError("dynamic_research_pair_signal_overperturbed")


def _request_context_without_marketing(request: DynamicEvidenceResearchRequest) -> object:
    return request.model_dump(exclude={"session_id", "marketing_context"}, mode="json")


def _context_without_perturbation(
    context: MarketingContextPlanningProjection,
    signal_id: str,
) -> object:
    payload = cast(
        "dict[str, object]",
        context.model_dump(exclude={"snapshot_id", "snapshot_sha256"}, mode="json"),
    )
    signals = cast("list[dict[str, object]]", payload["customer_signals"])
    payload["customer_signals"] = [
        {
            key: value
            for key, value in signal.items()
            if not (signal.get("signal_id") == signal_id and key in {"summary", "signal_sha256"})
        }
        for signal in signals
    ]
    return payload


def _require_comparable_runtimes(
    candidate: DynamicResearchRuntimeIdentity,
    baseline: DynamicResearchRuntimeIdentity,
) -> None:
    candidate_core = candidate.model_dump(exclude={"runner_id", "planner_protocol_sha256"})
    baseline_core = baseline.model_dump(exclude={"runner_id", "planner_protocol_sha256"})
    if candidate.runner_id == baseline.runner_id or candidate_core != baseline_core:
        raise DynamicResearchCanaryError("dynamic_research_runtimes_not_comparable")


def _require_fresh_observations(results: tuple[DynamicResearchTrialResult, ...]) -> None:
    nonces = tuple(item.observation.trial_nonce_sha256 for item in results)
    if len(set(nonces)) != len(nonces):
        raise DynamicResearchCanaryError("dynamic_research_trial_reused")


def _blind_preference_statistics(
    cases: tuple[DynamicResearchCanaryCase, ...],
    *,
    trials: int,
    preferences: tuple[DynamicResearchBlindPreference, ...],
) -> tuple[float | None, float | None, bool]:
    if not preferences:
        return None, None, False
    valid_keys = {
        (item.expectation.counterfactual_pair_id, trial)
        for item in cases
        for trial in range(1, trials + 1)
    }
    seen: set[tuple[str, int, str]] = set()
    counts: dict[tuple[str, int], int] = {}
    for item in preferences:
        key = (item.pair_id, item.trial)
        identity = (*key, item.rater_id)
        if key not in valid_keys or identity in seen:
            raise DynamicResearchCanaryError("dynamic_research_blind_preference_invalid")
        seen.add(identity)
        counts[key] = counts.get(key, 0) + 1
    complete = all(counts.get(key, 0) >= _MIN_RATERS_PER_PAIR for key in valid_keys)
    candidate_wins = sum(item.winner == "candidate" for item in preferences)
    baseline_wins = sum(item.winner == "baseline" for item in preferences)
    decisive = candidate_wins + baseline_wins
    if decisive == 0:
        return 0.5, 0.0, complete
    rate = candidate_wins / decisive
    return rate, _wilson_lower_bound(candidate_wins, decisive), complete


def _wilson_lower_bound(successes: int, count: int) -> float:
    proportion = successes / count
    denominator = 1 + (_Z_95**2 / count)
    centre = proportion + (_Z_95**2 / (2 * count))
    spread = _Z_95 * math.sqrt((proportion * (1 - proportion) + _Z_95**2 / (4 * count)) / count)
    return (centre - spread) / denominator


def _semantic_fields(steps: tuple[DynamicResearchPlanStep, ...]) -> dict[SemanticField, str]:
    return {
        "research_questions": " ".join(_normalize_text(item.research_question) for item in steps),
        "counter_evidence_questions": " ".join(
            _normalize_text(item.counter_evidence_question) for item in steps
        ),
    }


def _plan_step(
    iteration: int,
    prompt: str,
    schema: JsonObject,
    output: JsonObject,
) -> DynamicResearchPlanStep:
    return DynamicResearchPlanStep.model_validate(
        {
            "iteration": iteration,
            **output,
            "prompt_sha256": sha256(prompt.encode()).hexdigest(),
            "output_schema_sha256": _json_sha256(schema),
        }
    )


def _json_sha256(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _elapsed_milliseconds(started: float) -> int:
    return min(3_600_000, max(0, round((time.monotonic() - started) * 1000)))


def _failure_code(error: Exception) -> str:
    return str(error).split(":", 1)[0][:300] or type(error).__name__
