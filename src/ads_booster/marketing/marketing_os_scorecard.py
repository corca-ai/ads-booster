"""Pure, offline trace grading for the versioned Marketing OS regression corpus.

The evaluated runner sees only a product packet and research scope contract. It returns canonical
terminal traces and, when research completed, the exact brief it attempted to launch. The scorecard
replays those traces and derives its own budget, lineage, containment, process, and outcome grades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, cast

from pydantic import Field, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    FeatureEvidencePacket,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.evidence_research_operator import ResearchObservation, ResearchScope
from ads_booster.marketing.feature_launch_evidence_brief import FeatureLaunchEvidenceBrief
from ads_booster.marketing.feature_launch_operator import (
    DecisionProposal,
    FeatureLaunchObservation,
)
from ads_booster.marketing.runtime import (
    AgentSession,
    MarketingRuntimeError,
    SessionEvent,
    canonical_json_object,
    replay_session,
    session_trace_sha256,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

_SCORECARD_SCHEMA_VERSION = "trace.marketing-os-scorecard.v1"
_CORPUS_SCHEMA_VERSION = "trace.marketing-os-corpus.v1"
_RESEARCH_EVENT = "research_observation_recorded"
_BRIEF_EVENT = "feature_launch_brief_committed"
_DECISION_EVENT = "feature_decision_committed"
_LAUNCH_OBSERVATION_EVENT = "feature_observation_recorded"
_STOPPED_EVENT = "feature_stopped"

ScorecardTerminalState = Literal["completed", "inconclusive", "not_started"]


class MarketingOsScorecardError(ValueError):
    """A trace, corpus, or runner violates deterministic offline evaluation semantics."""


class MarketingOsEvalInput(ContractModel):
    """The only case data an evaluated runner is permitted to receive."""

    schema_version: Literal["trace.marketing-os-scorecard-input.v2"]
    case_id: AgentIdentifier
    feature_packet: FeatureEvidencePacket
    required_scopes: Annotated[tuple[ResearchScope, ...], Field(min_length=1, max_length=3)]
    max_tool_calls: Annotated[int, Field(ge=1, le=6)]
    max_cost_units: Annotated[int, Field(ge=1, le=12)]

    @model_validator(mode="after")
    def require_consistent_scope_and_packet(self) -> MarketingOsEvalInput:
        if len(set(self.required_scopes)) != len(self.required_scopes):
            raise ValueError("required research scopes must be unique")
        return self


class MarketingOsEvalExpectation(ContractModel):
    """Grader-only trajectory requirements; never passed to an evaluated runner."""

    schema_version: Literal["trace.marketing-os-scorecard-expectation.v2"]
    case_id: AgentIdentifier
    expected_research_state: ScorecardTerminalState
    expected_launch_state: ScorecardTerminalState
    expected_research_process_passed: bool
    expected_research_outcome_ready: bool
    expected_brief_lineage_verified: bool
    expected_claim_contained: bool
    expected_launch_tool_calls: Annotated[int, Field(ge=0, le=1)]
    expected_launch_process_passed: bool | None = None
    expected_launch_outcome_passed: bool | None = None
    required_reason_codes: Annotated[tuple[str, ...], Field(max_length=12)] = ()

    @model_validator(mode="after")
    def require_consistent_launch_expectation(self) -> MarketingOsEvalExpectation:
        launch_absent = self.expected_launch_state == "not_started"
        if launch_absent and (
            self.expected_launch_process_passed is not None
            or self.expected_launch_outcome_passed is not None
            or self.expected_launch_tool_calls != 0
        ):
            raise ValueError("an unstarted launch cannot have grades or tool calls")
        if not launch_absent and (
            self.expected_launch_process_passed is None
            or self.expected_launch_outcome_passed is None
        ):
            raise ValueError("a started launch requires both process and outcome expectations")
        if len(set(self.required_reason_codes)) != len(self.required_reason_codes):
            raise ValueError("required reason codes must be unique")
        return self


@dataclass(frozen=True, slots=True)
class MarketingOsEvalCase:
    """One corpus record. Its reference is held by the grader, not the runner."""

    input: MarketingOsEvalInput
    expectation: MarketingOsEvalExpectation

    def __post_init__(self) -> None:
        """Keep runner input and private grader reference paired by a stable opaque case ID."""
        if self.input.case_id != self.expectation.case_id:
            raise MarketingOsScorecardError("scorecard_case_identifier_mismatch")


class MarketingOsTraceEvent(ContractModel):
    """One canonical runtime event supplied for independent replay by the scorecard."""

    sequence: Annotated[int, Field(ge=1)]
    event_type: Annotated[str, Field(min_length=1, max_length=120)]
    payload_json: Annotated[str, Field(min_length=2, max_length=200_000)]
    payload_sha256: Sha256Digest
    occurred_at: datetime

    @model_validator(mode="after")
    def require_canonical_payload(self) -> MarketingOsTraceEvent:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            raise ValueError("trace event time must be UTC")
        payload = _event_payload(self)
        if canonical_json_object(payload) != self.payload_json:
            raise ValueError("trace payload is not canonical JSON")
        if sha256(self.payload_json.encode()).hexdigest() != self.payload_sha256:
            raise ValueError("trace payload digest mismatch")
        return self


class MarketingOsSessionTrace(ContractModel):
    """Terminal exported events; no mutable runtime checkpoint is accepted."""

    schema_version: Literal["trace.marketing-os-session-trace.v1"]
    events: Annotated[tuple[MarketingOsTraceEvent, ...], Field(min_length=2, max_length=256)]

    @model_validator(mode="after")
    def require_terminal_event_order(self) -> MarketingOsSessionTrace:
        if self.events[0].event_type != "session_started":
            raise ValueError("scorecard trace must start with runtime header")
        if self.events[-1].event_type != "session_finalized":
            raise ValueError("scorecard trace must end with finalization")
        if tuple(event.sequence for event in self.events) != tuple(range(1, len(self.events) + 1)):
            raise ValueError("scorecard trace sequences must be continuous")
        event_times = tuple(event.occurred_at for event in self.events)
        if event_times != tuple(sorted(event_times)):
            raise ValueError("scorecard trace times must be nondecreasing")
        return self


class MarketingOsEvalObservation(ContractModel):
    """Raw canonical material from which the grader derives every score-relevant fact."""

    schema_version: Literal["trace.marketing-os-scorecard-observation.v2"]
    case_id: AgentIdentifier
    research_trace: MarketingOsSessionTrace
    launch_brief: FeatureLaunchEvidenceBrief | None = None
    launch_trace: MarketingOsSessionTrace | None = None

    @model_validator(mode="after")
    def require_launch_material_consistency(self) -> MarketingOsEvalObservation:
        if self.launch_trace is not None and self.launch_brief is None:
            raise ValueError("a launch trace requires the attempted evidence brief")
        return self


class MarketingOsRunnerMetadata(ContractModel):
    """Pinned runner identity for comparable repeated scorecard trials."""

    schema_version: Literal["trace.marketing-os-scorecard-runner.v1"]
    runner_id: AgentIdentifier
    runner_sha256: Sha256Digest
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    prompt_sha256: Sha256Digest
    skill_registry_sha256: Sha256Digest
    trial: Annotated[int, Field(ge=1, le=1000)]


class MarketingOsTraceAssessment(ContractModel):
    """Scorecard-derived process and environment evidence, never supplied by a runner."""

    research_state: ScorecardTerminalState
    launch_state: ScorecardTerminalState
    research_process_passed: bool
    research_outcome_ready: bool
    research_vertical_trace_valid: bool
    launch_process_passed: bool | None
    launch_outcome_passed: bool | None
    launch_vertical_trace_valid: bool | None
    brief_lineage_verified: bool
    claim_contained: bool
    research_tool_calls: Annotated[int, Field(ge=0, le=12)]
    launch_tool_calls: Annotated[int, Field(ge=0, le=12)]
    spent_cost_units: Annotated[int, Field(ge=0, le=48)]
    reason_codes: Annotated[tuple[str, ...], Field(max_length=24)] = ()


class MarketingOsEvalResult(ContractModel):
    schema_version: Literal["trace.marketing-os-scorecard-result.v2"]
    case_id: AgentIdentifier
    process_passed: bool
    environment_passed: bool
    passed: bool
    process_reasons: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    environment_reasons: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    assessment: MarketingOsTraceAssessment

    @model_validator(mode="after")
    def require_combined_verdict(self) -> MarketingOsEvalResult:
        if self.passed != (self.process_passed and self.environment_passed):
            raise ValueError("combined verdict must equal process and environment verdicts")
        return self


class MarketingOsScorecardReport(ContractModel):
    schema_version: Literal["trace.marketing-os-scorecard-report.v1"]
    suite_id: Literal["trace.marketing-os-scorecard.v1"]
    corpus_sha256: Sha256Digest
    runner: MarketingOsRunnerMetadata
    results: Annotated[tuple[MarketingOsEvalResult, ...], Field(min_length=1, max_length=64)]
    process_pass_count: Annotated[int, Field(ge=0, le=64)]
    environment_pass_count: Annotated[int, Field(ge=0, le=64)]
    pass_count: Annotated[int, Field(ge=0, le=64)]

    @model_validator(mode="after")
    def require_aggregate_counts(self) -> MarketingOsScorecardReport:
        case_ids = tuple(result.case_id for result in self.results)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("scorecard result IDs must be unique")
        if self.process_pass_count != sum(result.process_passed for result in self.results):
            raise ValueError("process pass count does not match results")
        if self.environment_pass_count != sum(result.environment_passed for result in self.results):
            raise ValueError("environment pass count does not match results")
        if self.pass_count != sum(result.passed for result in self.results):
            raise ValueError("pass count does not match results")
        return self


class MarketingOsScorecardThreshold(ContractModel):
    schema_version: Literal["trace.marketing-os-scorecard-threshold.v1"]
    required_case_count: Annotated[int, Field(ge=1, le=64)]
    minimum_process_pass_count: Annotated[int, Field(ge=0, le=64)]
    minimum_environment_pass_count: Annotated[int, Field(ge=0, le=64)]
    minimum_pass_count: Annotated[int, Field(ge=0, le=64)]

    @model_validator(mode="after")
    def require_reachable_thresholds(self) -> MarketingOsScorecardThreshold:
        values = (
            self.minimum_process_pass_count,
            self.minimum_environment_pass_count,
            self.minimum_pass_count,
        )
        if any(value > self.required_case_count for value in values):
            raise ValueError("scorecard threshold exceeds its required case count")
        return self


class MarketingOsScorecardComparison(ContractModel):
    schema_version: Literal["trace.marketing-os-scorecard-comparison.v1"]
    corpus_sha256: Sha256Digest
    baseline_runner_id: AgentIdentifier
    candidate_runner_id: AgentIdentifier
    process_pass_delta: int
    environment_pass_delta: int
    pass_delta: int


class MarketingOsScorecardRunner(Protocol):
    def run(self, case: MarketingOsEvalInput) -> MarketingOsEvalObservation: ...


class MarketingOsVerticalTraceVerifier(Protocol):
    """Validate replayed vertical traces against the pinned registry and evaluator authority."""

    def validate_research(self, case: MarketingOsEvalInput, session: AgentSession) -> None: ...

    def rederive_brief(
        self,
        case: MarketingOsEvalInput,
        research: AgentSession,
        brief: FeatureLaunchEvidenceBrief,
    ) -> None: ...

    def validate_launch(
        self,
        case: MarketingOsEvalInput,
        research: AgentSession,
        brief: FeatureLaunchEvidenceBrief,
        session: AgentSession,
    ) -> None: ...


class MarketingOsScorecard:
    """Grade versioned runner inputs from independently replayed trace material."""

    def __init__(self, verifier: MarketingOsVerticalTraceVerifier) -> None:
        self._verifier: MarketingOsVerticalTraceVerifier = verifier

    def evaluate(
        self,
        cases: tuple[MarketingOsEvalCase, ...],
        runner: MarketingOsScorecardRunner,
        metadata: MarketingOsRunnerMetadata,
    ) -> MarketingOsScorecardReport:
        _validate_cases(cases)
        results = tuple(self.grade(case, _run_case(runner, case.input)) for case in cases)
        return MarketingOsScorecardReport(
            schema_version="trace.marketing-os-scorecard-report.v1",
            suite_id=_SCORECARD_SCHEMA_VERSION,
            corpus_sha256=marketing_os_corpus_sha256(cases),
            runner=metadata,
            results=results,
            process_pass_count=sum(result.process_passed for result in results),
            environment_pass_count=sum(result.environment_passed for result in results),
            pass_count=sum(result.passed for result in results),
        )

    def grade(
        self,
        case: MarketingOsEvalCase,
        observation: MarketingOsEvalObservation,
    ) -> MarketingOsEvalResult:
        if observation.case_id != case.input.case_id:
            raise MarketingOsScorecardError("scorecard_observation_identifier_mismatch")
        assessment = _derive_assessment(case.input, observation, self._verifier)
        process_reasons = _process_reasons(case.input, case.expectation, assessment)
        environment_reasons = _environment_reasons(case.expectation, assessment)
        process_passed = not process_reasons
        environment_passed = not environment_reasons
        return MarketingOsEvalResult(
            schema_version="trace.marketing-os-scorecard-result.v2",
            case_id=case.input.case_id,
            process_passed=process_passed,
            environment_passed=environment_passed,
            passed=process_passed and environment_passed,
            process_reasons=process_reasons,
            environment_reasons=environment_reasons,
            assessment=assessment,
        )

    @staticmethod
    def require_threshold(
        report: MarketingOsScorecardReport,
        threshold: MarketingOsScorecardThreshold,
    ) -> None:
        failures = _threshold_failures(report, threshold)
        if failures:
            raise MarketingOsScorecardError(";".join(failures))


def marketing_os_corpus_sha256(cases: tuple[MarketingOsEvalCase, ...]) -> str:
    """Return one canonical digest for runner-comparable inputs and private grader references."""
    _validate_cases(cases)
    encoded = json.dumps(
        {
            "schema_version": _CORPUS_SCHEMA_VERSION,
            "cases": [
                {
                    "input": item.input.model_dump(mode="json"),
                    "expectation": item.expectation.model_dump(mode="json"),
                }
                for item in cases
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(encoded.encode()).hexdigest()


def compare_marketing_os_scorecards(
    baseline: MarketingOsScorecardReport,
    candidate: MarketingOsScorecardReport,
) -> MarketingOsScorecardComparison:
    if baseline.corpus_sha256 != candidate.corpus_sha256:
        raise MarketingOsScorecardError("scorecard_comparison_corpus_mismatch")
    return MarketingOsScorecardComparison(
        schema_version="trace.marketing-os-scorecard-comparison.v1",
        corpus_sha256=baseline.corpus_sha256,
        baseline_runner_id=baseline.runner.runner_id,
        candidate_runner_id=candidate.runner.runner_id,
        process_pass_delta=candidate.process_pass_count - baseline.process_pass_count,
        environment_pass_delta=(candidate.environment_pass_count - baseline.environment_pass_count),
        pass_delta=candidate.pass_count - baseline.pass_count,
    )


def scorecard_trace_from_session(session: AgentSession) -> MarketingOsSessionTrace:
    """Export one terminal runtime trace without exposing its mutable checkpoint fields."""
    return MarketingOsSessionTrace(
        schema_version="trace.marketing-os-session-trace.v1",
        events=tuple(
            MarketingOsTraceEvent(
                sequence=event.sequence,
                event_type=event.event_type,
                payload_json=canonical_json_object(event.payload),
                payload_sha256=event.payload_sha256,
                occurred_at=event.occurred_at,
            )
            for event in session.events
        ),
    )


def _run_case(
    runner: MarketingOsScorecardRunner,
    case: MarketingOsEvalInput,
) -> MarketingOsEvalObservation:
    observation = runner.run(case)
    if observation.case_id != case.case_id:
        raise MarketingOsScorecardError("scorecard_observation_identifier_mismatch")
    return observation


def _validate_cases(cases: tuple[MarketingOsEvalCase, ...]) -> None:
    if not cases:
        raise MarketingOsScorecardError("scorecard_corpus_empty")
    case_ids = tuple(item.input.case_id for item in cases)
    if len(set(case_ids)) != len(case_ids):
        raise MarketingOsScorecardError("scorecard_corpus_duplicate_case")


def _derive_assessment(
    input_case: MarketingOsEvalInput,
    observation: MarketingOsEvalObservation,
    verifier: MarketingOsVerticalTraceVerifier,
) -> MarketingOsTraceAssessment:
    research = _replay_trace(observation.research_trace)
    research_observations = _models(research, _RESEARCH_EVENT, ResearchObservation)
    research_ready = all(
        any(
            observation.scope is scope and observation.evidence_status == "sufficient"
            for observation in research_observations
        )
        for scope in input_case.required_scopes
    )
    research_verified = _verification_passes(
        lambda: verifier.validate_research(input_case, research)
    )
    research_process = (
        _research_process_passes(research, research_observations) and research_verified
    )
    research_reasons = tuple(
        f"missing_scope:{scope}"
        for scope in input_case.required_scopes
        if not any(
            observation.scope is scope and observation.evidence_status == "sufficient"
            for observation in research_observations
        )
    )
    if observation.launch_trace is None:
        launch_brief = observation.launch_brief
        brief_lineage = _brief_lineage_without_launch(input_case, research, launch_brief)
        if brief_lineage and launch_brief is not None:
            brief_lineage = _verification_passes(
                lambda: verifier.rederive_brief(input_case, research, launch_brief)
            )
        verification_reasons = () if research_verified else ("research_vertical_trace_unverified",)
        reason_codes = (
            (*research_reasons, *verification_reasons, "feature_launch_evidence_brief_unverified")
            if observation.launch_brief is not None and not brief_lineage
            else (*research_reasons, *verification_reasons)
        )
        return MarketingOsTraceAssessment(
            research_state=_terminal_state(research),
            launch_state="not_started",
            research_process_passed=research_process,
            research_outcome_ready=research_ready,
            research_vertical_trace_valid=research_verified,
            launch_process_passed=None,
            launch_outcome_passed=None,
            launch_vertical_trace_valid=None,
            brief_lineage_verified=brief_lineage,
            claim_contained=True,
            research_tool_calls=research.tool_calls,
            launch_tool_calls=0,
            spent_cost_units=research.spent_cost_units,
            reason_codes=reason_codes,
        )

    launch = _replay_trace(observation.launch_trace)
    brief = observation.launch_brief
    if brief is None:
        raise MarketingOsScorecardError("scorecard_launch_brief_missing")
    persisted_briefs = _models(launch, _BRIEF_EVENT, FeatureLaunchEvidenceBrief)
    if len(persisted_briefs) != 1 or persisted_briefs[0] != brief:
        raise MarketingOsScorecardError("scorecard_launch_brief_trace_mismatch")
    proposals = _models(launch, _DECISION_EVENT, DecisionProposal)
    launch_observations = _models(launch, _LAUNCH_OBSERVATION_EVENT, FeatureLaunchObservation)
    brief_lineage = _brief_lineage_without_launch(input_case, research, brief)
    if brief_lineage:
        brief_lineage = _verification_passes(
            lambda: verifier.rederive_brief(input_case, research, brief)
        )
    claim_contained = _claim_contained(input_case, brief, proposals)
    launch_verified = brief_lineage and _verification_passes(
        lambda: verifier.validate_launch(input_case, research, brief, launch)
    )
    launch_process = (
        _launch_process_passes(launch, proposals, launch_observations, brief_lineage)
        and launch_verified
    )
    launch_outcome = _launch_outcome_passes(
        launch_observations,
        brief_lineage,
        claim_contained,
    )
    reason_codes = tuple(
        dict.fromkeys(
            (
                *research_reasons,
                *(("research_vertical_trace_unverified",) if not research_verified else ()),
                *(("launch_vertical_trace_unverified",) if not launch_verified else ()),
                *_stopped_reasons(launch),
                *(
                    ("counter_evidence_found",)
                    if _counter_evidence_found(launch_observations)
                    else ()
                ),
            )
        )
    )
    return MarketingOsTraceAssessment(
        research_state=_terminal_state(research),
        launch_state=_terminal_state(launch),
        research_process_passed=research_process,
        research_outcome_ready=research_ready,
        research_vertical_trace_valid=research_verified,
        launch_process_passed=launch_process,
        launch_outcome_passed=launch_outcome,
        launch_vertical_trace_valid=launch_verified,
        brief_lineage_verified=brief_lineage,
        claim_contained=claim_contained,
        research_tool_calls=research.tool_calls,
        launch_tool_calls=launch.tool_calls,
        spent_cost_units=research.spent_cost_units + launch.spent_cost_units,
        reason_codes=reason_codes,
    )


def _replay_trace(trace: MarketingOsSessionTrace) -> AgentSession:
    events = tuple(
        SessionEvent(
            sequence=event.sequence,
            event_type=event.event_type,
            payload=_event_payload(event),
            payload_sha256=event.payload_sha256,
            occurred_at=event.occurred_at,
        )
        for event in trace.events
    )
    try:
        return replay_session(events)
    except MarketingRuntimeError as error:
        raise MarketingOsScorecardError("scorecard_trace_replay_invalid") from error


def _event_payload(event: MarketingOsTraceEvent) -> JsonObject:
    try:
        value = cast("object", json.loads(event.payload_json))
    except json.JSONDecodeError as error:
        raise ValueError("trace payload must be JSON") from error
    if not isinstance(value, dict):
        raise ValueError("trace payload must be an object")
    return cast("JsonObject", value)


def _terminal_state(session: AgentSession) -> ScorecardTerminalState:
    if session.state.value == "completed":
        return "completed"
    if session.state.value == "inconclusive":
        return "inconclusive"
    raise MarketingOsScorecardError("scorecard_trace_not_terminal")


def _models[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> tuple[T, ...]:
    try:
        return tuple(
            model.model_validate(event.payload)
            for event in session.events
            if event.event_type == event_type
        )
    except ValueError as error:
        raise MarketingOsScorecardError("scorecard_trace_contract_invalid") from error


def _research_process_passes(
    session: AgentSession,
    observations: tuple[ResearchObservation, ...],
) -> bool:
    event_types = tuple(event.event_type for event in session.events)
    return (
        session.state.value in {"completed", "inconclusive"}
        and len(observations) == session.tool_calls
        and event_types.count("tool_dispatched") == session.tool_calls
        and event_types.count("tool_succeeded") == session.tool_calls
        and all(observation.receipt_sha256 for observation in observations)
    )


def _brief_lineage_without_launch(
    input_case: MarketingOsEvalInput,
    research: AgentSession,
    brief: FeatureLaunchEvidenceBrief | None,
) -> bool:
    return bool(
        brief is not None
        and brief.feature_packet_id == input_case.feature_packet.packet_id
        and brief.feature_packet_sha256 == contract_sha256(input_case.feature_packet)
        and brief.research_session_id == research.session_id
        and brief.research_trace_sha256 == session_trace_sha256(research)
    )


def _claim_contained(
    input_case: MarketingOsEvalInput,
    brief: FeatureLaunchEvidenceBrief,
    proposals: tuple[DecisionProposal, ...],
) -> bool:
    if len(proposals) != 1:
        return False
    proposal = proposals[0]
    supported_claim_ids = {
        claim_id
        for evidence in brief.evidence
        for claim_id in evidence.supported_allowed_claim_ids
        if evidence.research_observation_id in proposal.research_observation_ids
    }
    return set(proposal.claim_ids).issubset(
        input_case.feature_packet.gate.allowed_claim_ids
    ) and set(proposal.claim_ids).issubset(supported_claim_ids)


def _launch_process_passes(
    session: AgentSession,
    proposals: tuple[DecisionProposal, ...],
    observations: tuple[FeatureLaunchObservation, ...],
    brief_lineage: bool,
) -> bool:
    event_types = tuple(event.event_type for event in session.events)
    required = (
        _BRIEF_EVENT,
        "feature_goal_committed",
        _DECISION_EVENT,
        "tool_dispatched",
        "tool_execution_started",
        "tool_succeeded",
        _LAUNCH_OBSERVATION_EVENT,
    )
    positions = tuple(event_types.index(event) for event in required if event in event_types)
    return (
        brief_lineage
        and len(proposals) == 1
        and len(observations) == 1
        and all(event in event_types for event in required)
        and positions == tuple(sorted(positions))
        and session.tool_calls == 1
    )


def _launch_outcome_passes(
    observations: tuple[FeatureLaunchObservation, ...],
    brief_lineage: bool,
    claim_contained: bool,
) -> bool:
    return bool(
        brief_lineage
        and claim_contained
        and len(observations) == 1
        and observations[0].evidence_status == "sufficient"
        and not observations[0].counter_evidence_found
    )


def _stopped_reasons(session: AgentSession) -> tuple[str, ...]:
    reasons: list[str] = []
    for event in session.events:
        if event.event_type != _STOPPED_EVENT:
            continue
        reason = event.payload.get("reason")
        if isinstance(reason, str):
            reasons.append(reason)
    return tuple(reasons)


def _counter_evidence_found(observations: tuple[FeatureLaunchObservation, ...]) -> bool:
    return any(observation.counter_evidence_found for observation in observations)


def _verification_passes(callback: Callable[[], None]) -> bool:
    try:
        callback()
    except ValueError:
        return False
    return True


def _process_reasons(
    input_case: MarketingOsEvalInput,
    expectation: MarketingOsEvalExpectation,
    assessment: MarketingOsTraceAssessment,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not assessment.research_vertical_trace_valid:
        reasons.append("research_vertical_trace_invalid")
    if assessment.launch_state != "not_started" and not assessment.launch_vertical_trace_valid:
        reasons.append("launch_vertical_trace_invalid")
    if assessment.research_process_passed != expectation.expected_research_process_passed:
        reasons.append("research_process_grade_mismatch")
    if assessment.launch_process_passed != expectation.expected_launch_process_passed:
        reasons.append("launch_process_grade_mismatch")
    if assessment.brief_lineage_verified != expectation.expected_brief_lineage_verified:
        reasons.append("brief_lineage_grade_mismatch")
    if assessment.claim_contained != expectation.expected_claim_contained:
        reasons.append("claim_containment_grade_mismatch")
    if assessment.launch_tool_calls != expectation.expected_launch_tool_calls:
        reasons.append("launch_tool_call_count_mismatch")
    if assessment.research_tool_calls + assessment.launch_tool_calls > input_case.max_tool_calls:
        reasons.append("tool_call_budget_exceeded")
    if assessment.spent_cost_units > input_case.max_cost_units:
        reasons.append("cost_budget_exceeded")
    return tuple(reasons)


def _environment_reasons(
    expectation: MarketingOsEvalExpectation,
    assessment: MarketingOsTraceAssessment,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if assessment.research_state != expectation.expected_research_state:
        reasons.append("research_terminal_state_mismatch")
    if assessment.launch_state != expectation.expected_launch_state:
        reasons.append("launch_terminal_state_mismatch")
    if assessment.research_outcome_ready != expectation.expected_research_outcome_ready:
        reasons.append("research_outcome_grade_mismatch")
    if assessment.launch_outcome_passed != expectation.expected_launch_outcome_passed:
        reasons.append("launch_outcome_grade_mismatch")
    missing_reasons = set(expectation.required_reason_codes) - set(assessment.reason_codes)
    if missing_reasons:
        reasons.append("required_reason_code_missing")
    return tuple(reasons)


def _threshold_failures(
    report: MarketingOsScorecardReport,
    threshold: MarketingOsScorecardThreshold,
) -> tuple[str, ...]:
    checks = (
        (len(report.results), threshold.required_case_count, "case_count_below_threshold"),
        (
            report.process_pass_count,
            threshold.minimum_process_pass_count,
            "process_score_below_threshold",
        ),
        (
            report.environment_pass_count,
            threshold.minimum_environment_pass_count,
            "environment_score_below_threshold",
        ),
        (report.pass_count, threshold.minimum_pass_count, "combined_score_below_threshold"),
    )
    return tuple(error for actual, minimum, error in checks if actual < minimum)
