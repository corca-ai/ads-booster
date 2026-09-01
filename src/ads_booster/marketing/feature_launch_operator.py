"""A receipt-grounded first vertical for feature-launch experiment design.

The operator has one observe-only skill. It proves that a planner can select a constrained marketing
action, replay a committed decision without another model call, and finish only from a receipt-bound
observation. It deliberately cannot publish, spend, contact customers, or mutate a control plane.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    FeatureEvidencePacket,
    OutcomeDefinition,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.feature_launch_evidence_brief import (
    FeatureLaunchEvidenceBrief,
    FeatureLaunchEvidenceBriefProjection,
    FeatureLaunchEvidenceBriefVerificationError,
    FeatureLaunchEvidenceBriefVerifier,
)
from ads_booster.marketing.planning_projections import FeaturePlanningProjection
from ads_booster.marketing.runtime import (
    AgentSession,
    EffectDisposition,
    MarketingAgentRuntime,
    RuntimeState,
    SessionStore,
    ToolAdmission,
    ToolBackend,
    ToolCall,
    ToolCapability,
    ToolReceipt,
    tool_receipt_from_event,
)
from ads_booster.transport.json_types import JsonObject


class FeatureLaunchOperatorError(ValueError):
    """A fail-closed feature-launch planning or lineage error."""


class MarketingGoal(ContractModel):
    schema_version: Literal["trace.marketing-goal.v1"]
    goal_id: AgentIdentifier
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    outcome: Literal["feature_launch_experiment"]
    pinned_skill_registry_sha256: Sha256Digest
    max_iterations: Literal[1] = 1
    stop_on_insufficient_evidence: Literal[True] = True


class DecisionProposal(ContractModel):
    schema_version: Literal["trace.feature-launch-decision.v1"]
    proposal_id: AgentIdentifier
    goal_id: AgentIdentifier
    skill_id: Literal["feature_launch_experiment.v1"]
    skill_sha256: Sha256Digest
    action_id: Literal["observe.feature_launch_experiment"]
    evidence_brief_sha256: Sha256Digest
    research_observation_ids: Annotated[
        tuple[AgentIdentifier, ...], Field(min_length=1, max_length=3)
    ]
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]
    control_frame: Annotated[str, Field(min_length=1, max_length=1000)]
    challenger_frame: Annotated[str, Field(min_length=1, max_length=1000)]
    counter_evidence_question: Annotated[str, Field(min_length=1, max_length=1000)]
    falsifier: Annotated[str, Field(min_length=1, max_length=1000)]
    measurement: OutcomeDefinition

    @model_validator(mode="after")
    def require_distinct_experiment_arms(self) -> Self:
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("decision claim IDs must be unique")
        if len(set(self.research_observation_ids)) != len(self.research_observation_ids):
            raise ValueError("decision research observation IDs must be unique")
        if self.control_frame == self.challenger_frame:
            raise ValueError("control and challenger frames must differ")
        return self


class FeatureLaunchObservation(ContractModel):
    schema_version: Literal["trace.feature-launch-observation.v1"]
    observation_id: AgentIdentifier
    receipt_sha256: Sha256Digest
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    feature_packet_sha256: Sha256Digest
    evidence_brief_sha256: Sha256Digest
    research_observation_ids: Annotated[
        tuple[AgentIdentifier, ...], Field(min_length=1, max_length=3)
    ]
    proposal_sha256: Sha256Digest
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    evidence_status: Literal["sufficient", "insufficient"]
    counter_evidence_found: bool
    observed_at: datetime

    @model_validator(mode="after")
    def require_utc_observation_time(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(
            self.observed_at
        ):
            raise ValueError("observed_at must be UTC")
        if len(set(self.research_observation_ids)) != len(self.research_observation_ids):
            raise ValueError("observation research IDs must be unique")
        return self


class FeatureLaunchEvaluation(ContractModel):
    schema_version: Literal["trace.feature-launch-evaluation.v1"]
    evaluation_id: AgentIdentifier
    goal_id: AgentIdentifier
    evidence_brief_sha256: Sha256Digest
    proposal_sha256: Sha256Digest
    observation_sha256: Sha256Digest
    process_passed: bool
    outcome_passed: bool
    state: Literal["completed", "inconclusive"]
    reasons: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    evaluated_at: datetime

    @model_validator(mode="after")
    def require_consistent_evaluation_state(self) -> Self:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() != UTC.utcoffset(
            self.evaluated_at
        ):
            raise ValueError("evaluated_at must be UTC")
        if (self.state == "completed") != (self.process_passed and self.outcome_passed):
            raise ValueError("completed requires both process and outcome assessments")
        return self


@dataclass(frozen=True, slots=True)
class AvailableAction:
    action_id: str
    capability: ToolCapability
    request_schema_sha256: str


@dataclass(frozen=True, slots=True)
class FeatureLaunchTask:
    goal: MarketingGoal
    feature_packet: FeatureEvidencePacket
    evidence_brief: FeatureLaunchEvidenceBrief


@dataclass(frozen=True, slots=True)
class FeatureLaunchPlanningContext:
    goal: MarketingGoal
    product: FeaturePlanningProjection
    evidence: FeatureLaunchEvidenceBriefProjection
    available_actions: tuple[AvailableAction, ...]


@dataclass(frozen=True, slots=True)
class Assessment:
    passed: bool
    reasons: tuple[str, ...]

    @classmethod
    def successful(cls, reason: str) -> Self:
        return cls(passed=True, reasons=(reason,))

    @classmethod
    def failed(cls, *reasons: str) -> Self:
        return cls(passed=False, reasons=reasons)


class FeatureLaunchPlanner(Protocol):
    def propose(self, context: FeatureLaunchPlanningContext) -> DecisionProposal: ...


class FeatureLaunchHand(ToolBackend, Protocol):
    def observation_for(self, receipt: ToolReceipt) -> FeatureLaunchObservation: ...


@dataclass(frozen=True, slots=True)
class FeatureLaunchSkillRegistry:
    snapshot_sha256: str
    skill_sha256: str
    action: AvailableAction

    def available_actions(self) -> tuple[AvailableAction, ...]:
        return (self.action,)

    def admit(self, task: FeatureLaunchTask, proposal: DecisionProposal) -> ToolAdmission:
        packet_sha256 = contract_sha256(task.feature_packet)
        brief_sha256 = contract_sha256(task.evidence_brief)
        _validate_feature_launch_action(self)
        _validate_feature_launch_task(task, self, packet_sha256)
        _validate_feature_launch_proposal(task, self, proposal, brief_sha256)
        input_sha256 = _canonical_sha256(
            {
                "goal": task.goal.model_dump(mode="json"),
                "feature_packet_sha256": packet_sha256,
                "evidence_brief_sha256": brief_sha256,
                "proposal": proposal.model_dump(mode="json"),
                "request_schema_sha256": self.action.request_schema_sha256,
            }
        )
        call = ToolCall(
            call_id=f"{task.goal.goal_id}-{proposal.proposal_id}",
            idempotency_key=f"{task.goal.goal_id}:{proposal.proposal_id}:{proposal.action_id}",
            capability_id=self.action.capability.capability_id,
            descriptor_sha256=self.action.capability.descriptor_sha256,
            input_sha256=input_sha256,
            effect_class=self.action.capability.effect_class,
        )
        return ToolAdmission(self.action.capability, call)


def _validate_feature_launch_action(registry: FeatureLaunchSkillRegistry) -> None:
    if registry.action.capability.effect_class != "observe":
        raise FeatureLaunchOperatorError("feature_launch_action_must_be_observe")


def _validate_feature_launch_task(
    task: FeatureLaunchTask,
    registry: FeatureLaunchSkillRegistry,
    packet_sha256: str,
) -> None:
    if task.goal.feature_packet_id != task.feature_packet.packet_id:
        raise FeatureLaunchOperatorError("goal_feature_packet_id_mismatch")
    if task.goal.feature_packet_sha256 != packet_sha256:
        raise FeatureLaunchOperatorError("goal_feature_packet_digest_mismatch")
    if task.evidence_brief.feature_packet_id != task.feature_packet.packet_id:
        raise FeatureLaunchOperatorError("brief_feature_packet_id_mismatch")
    if task.evidence_brief.feature_packet_sha256 != packet_sha256:
        raise FeatureLaunchOperatorError("brief_feature_packet_digest_mismatch")
    if task.goal.pinned_skill_registry_sha256 != registry.snapshot_sha256:
        raise FeatureLaunchOperatorError("goal_skill_registry_digest_mismatch")


def _validate_feature_launch_proposal(
    task: FeatureLaunchTask,
    registry: FeatureLaunchSkillRegistry,
    proposal: DecisionProposal,
    brief_sha256: str,
) -> None:
    if proposal.goal_id != task.goal.goal_id:
        raise FeatureLaunchOperatorError("proposal_goal_mismatch")
    if proposal.skill_sha256 != registry.skill_sha256:
        raise FeatureLaunchOperatorError("proposal_skill_digest_mismatch")
    if proposal.action_id != registry.action.action_id:
        raise FeatureLaunchOperatorError("proposal_action_not_available")
    if proposal.evidence_brief_sha256 != brief_sha256:
        raise FeatureLaunchOperatorError("proposal_evidence_brief_digest_mismatch")
    evidence_by_observation_id = {
        item.research_observation_id: item for item in task.evidence_brief.evidence
    }
    if not set(proposal.research_observation_ids).issubset(evidence_by_observation_id):
        raise FeatureLaunchOperatorError("proposal_research_observation_not_in_brief")
    supported_claim_ids = {
        claim_id
        for observation_id in proposal.research_observation_ids
        for claim_id in evidence_by_observation_id[observation_id].supported_allowed_claim_ids
    }
    if not set(proposal.claim_ids).issubset(task.feature_packet.gate.allowed_claim_ids):
        raise FeatureLaunchOperatorError("proposal_claim_not_allowed")
    if not set(proposal.claim_ids).issubset(supported_claim_ids):
        raise FeatureLaunchOperatorError("proposal_claim_not_supported_by_brief")


@dataclass(frozen=True, slots=True)
class FeatureLaunchDependencies:
    planner: FeatureLaunchPlanner
    registry: FeatureLaunchSkillRegistry
    hand: FeatureLaunchHand
    evaluator: FeatureLaunchEvaluator
    brief_verifier: FeatureLaunchEvidenceBriefVerifier


@dataclass(frozen=True, slots=True)
class FeatureLaunchRuntimeContext:
    store: SessionStore
    task: FeatureLaunchTask
    dependencies: FeatureLaunchDependencies
    now: datetime


@dataclass(frozen=True, slots=True)
class FeatureLaunchExecution:
    session: AgentSession
    task: FeatureLaunchTask
    proposal: DecisionProposal
    admission: ToolAdmission


class FeatureLaunchEvaluator:
    """Deterministic process and outcome graders for the first feature-launch skill."""

    def grade_process(
        self,
        session: AgentSession,
        execution: FeatureLaunchExecution,
        observation: FeatureLaunchObservation,
    ) -> Assessment:
        event_types = tuple(event.event_type for event in session.events)
        required = (
            "feature_launch_brief_committed",
            "feature_goal_committed",
            "feature_decision_committed",
            "tool_dispatched",
            "tool_execution_started",
            "tool_succeeded",
            "feature_observation_recorded",
        )
        reasons = [f"missing_event:{name}" for name in required if name not in event_types]
        positions = tuple(event_types.index(name) for name in required if name in event_types)
        if positions != tuple(sorted(positions)):
            reasons.append("trace_event_order_invalid")
        receipt = _latest_receipt(session)
        if receipt is None:
            reasons.append("missing_runtime_receipt")
        else:
            if (
                session.pending_call is not None
                or receipt.disposition is not EffectDisposition.SUCCEEDED
            ):
                reasons.append("unresolved_or_unsuccessful_tool")
            if (
                receipt.call_sha256 != execution.admission.call.digest
                or observation.call_sha256 != receipt.call_sha256
            ):
                reasons.append("receipt_call_lineage_mismatch")
            if observation.receipt_sha256 != receipt.receipt_sha256:
                reasons.append("observation_receipt_lineage_mismatch")
            if observation.request_sha256 != execution.admission.call.input_sha256:
                reasons.append("observation_request_lineage_mismatch")
        if reasons:
            return Assessment.failed(*reasons)
        return Assessment.successful("receipt_grounded_process")

    def grade_outcome(
        self,
        execution: FeatureLaunchExecution,
        observation: FeatureLaunchObservation,
    ) -> Assessment:
        reasons: list[str] = []
        if not set(execution.proposal.claim_ids).issubset(
            execution.task.feature_packet.gate.allowed_claim_ids
        ):
            reasons.append("claim_outside_feature_gate")
        if observation.feature_packet_sha256 != contract_sha256(execution.task.feature_packet):
            reasons.append("observation_feature_packet_mismatch")
        if observation.evidence_brief_sha256 != contract_sha256(execution.task.evidence_brief):
            reasons.append("observation_evidence_brief_mismatch")
        if observation.research_observation_ids != execution.proposal.research_observation_ids:
            reasons.append("observation_research_observation_mismatch")
        if observation.proposal_sha256 != contract_sha256(execution.proposal):
            reasons.append("observation_proposal_mismatch")
        if observation.evidence_status != "sufficient":
            reasons.append("insufficient_evidence")
        if observation.counter_evidence_found:
            reasons.append("counter_evidence_found")
        if not execution.proposal.counter_evidence_question or not execution.proposal.falsifier:
            reasons.append("missing_counter_evidence_or_falsifier")
        if reasons:
            return Assessment.failed(*reasons)
        return Assessment.successful("claim_contained_measurable_experiment")

    def evaluate(
        self,
        execution: FeatureLaunchExecution,
        observation: FeatureLaunchObservation,
        *,
        now: datetime,
    ) -> FeatureLaunchEvaluation:
        process = self.grade_process(execution.session, execution, observation)
        outcome = self.grade_outcome(execution, observation)
        passed = process.passed and outcome.passed
        return FeatureLaunchEvaluation(
            schema_version="trace.feature-launch-evaluation.v1",
            evaluation_id=f"evaluation-{execution.task.goal.goal_id}",
            goal_id=execution.task.goal.goal_id,
            evidence_brief_sha256=contract_sha256(execution.task.evidence_brief),
            proposal_sha256=contract_sha256(execution.proposal),
            observation_sha256=contract_sha256(observation),
            process_passed=process.passed,
            outcome_passed=outcome.passed,
            state="completed" if passed else "inconclusive",
            reasons=(*process.reasons, *outcome.reasons),
            evaluated_at=now,
        )


class FeatureLaunchExperimentOperator:
    """Execute one replayable observe-only feature-launch experiment-design decision."""

    def __init__(self, runtime: MarketingAgentRuntime) -> None:
        self._runtime: MarketingAgentRuntime = runtime

    def run(self, session: AgentSession, context: FeatureLaunchRuntimeContext) -> AgentSession:
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            return session
        if session.state in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            _validate_terminal_feature_session(session, context)
            return session
        prepared = self._prepare(session, context)
        if isinstance(prepared, AgentSession):
            return prepared
        current = self._advance_tool(prepared, context)
        if current.state is RuntimeState.AWAITING_RECONCILIATION:
            return current
        return self._observe_evaluate_or_stop(current, prepared, context)

    def _prepare(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext
    ) -> FeatureLaunchExecution | AgentSession:
        current = self._commit_or_validate_brief(session, context)
        current = self._commit_or_validate_goal(current, context)
        if current.state in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            return current
        try:
            current, proposal = self._plan_or_replay(current, context)
            admission = context.dependencies.registry.admit(context.task, proposal)
        except FeatureLaunchOperatorError as error:
            return self._stop(current, context, str(error))
        return FeatureLaunchExecution(current, context.task, proposal, admission)

    def _commit_or_validate_brief(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext
    ) -> AgentSession:
        briefs = _models(session, "feature_launch_brief_committed", FeatureLaunchEvidenceBrief)
        if len(briefs) > 1:
            raise FeatureLaunchOperatorError("feature_launch_brief_count_exceeds_one")
        if briefs:
            if briefs[0] != context.task.evidence_brief:
                raise FeatureLaunchOperatorError("persisted_feature_launch_brief_mismatch")
            return session
        try:
            context.dependencies.brief_verifier.verify(context.task.evidence_brief)
        except FeatureLaunchEvidenceBriefVerificationError as error:
            raise FeatureLaunchOperatorError("feature_launch_evidence_brief_unverified") from error
        if any(
            event.event_type in {"feature_goal_committed", "feature_decision_committed"}
            for event in session.events
        ):
            raise FeatureLaunchOperatorError("feature_launch_brief_must_precede_goal")
        return self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="feature_launch_brief_committed",
            payload=_json_payload(context.task.evidence_brief),
            now=context.now,
        )

    def _commit_or_validate_goal(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext
    ) -> AgentSession:
        goals = _models(session, "feature_goal_committed", MarketingGoal)
        if len(goals) > 1:
            raise FeatureLaunchOperatorError("feature_goal_count_exceeds_one")
        if goals:
            if goals[0] != context.task.goal:
                raise FeatureLaunchOperatorError("persisted_goal_mismatch")
            return session
        return self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="feature_goal_committed",
            payload=_json_payload(context.task.goal),
            now=context.now,
        )

    def _plan_or_replay(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext
    ) -> tuple[AgentSession, DecisionProposal]:
        committed = _models(session, "feature_decision_committed", DecisionProposal)
        if len(committed) > 1:
            raise FeatureLaunchOperatorError("feature_decision_count_exceeds_one")
        if committed:
            return session, committed[0]
        proposal = context.dependencies.planner.propose(
            FeatureLaunchPlanningContext(
                context.task.goal,
                FeaturePlanningProjection.from_packet(context.task.feature_packet),
                FeatureLaunchEvidenceBriefProjection.from_brief(context.task.evidence_brief),
                context.dependencies.registry.available_actions(),
            )
        )
        if proposal.goal_id != context.task.goal.goal_id:
            raise FeatureLaunchOperatorError("planner_proposal_goal_mismatch")
        updated = self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="feature_decision_committed",
            payload=_json_payload(proposal),
            now=context.now,
        )
        return updated, proposal

    def _advance_tool(
        self, execution: FeatureLaunchExecution, context: FeatureLaunchRuntimeContext
    ) -> AgentSession:
        current = execution.session
        if current.pending_call is not None:
            if current.execution_started:
                return self._runtime.reconcile_interrupted_execution(
                    context.store, current, now=context.now
                )
        elif _latest_receipt(current) is None:
            current = self._runtime.request_persisted_tool(
                context.store, current, execution.admission, now=context.now
            )
        if current.pending_call is None:
            return current
        return self._runtime.execute_persisted_tool(
            context.store, current, context.dependencies.hand, now=context.now
        )

    def _observe_evaluate_or_stop(
        self,
        session: AgentSession,
        execution: FeatureLaunchExecution,
        context: FeatureLaunchRuntimeContext,
    ) -> AgentSession:
        observed = self._record_or_replay_observation(session, execution, context)
        if isinstance(observed, AgentSession):
            return observed
        current, observation = observed
        return self._evaluate_or_replay(current, execution, observation, context)

    def _record_or_replay_observation(
        self,
        session: AgentSession,
        execution: FeatureLaunchExecution,
        context: FeatureLaunchRuntimeContext,
    ) -> tuple[AgentSession, FeatureLaunchObservation] | AgentSession:
        receipt = _latest_receipt(session)
        if receipt is None:
            return self._stop(session, context, "missing_runtime_receipt")
        if receipt.disposition is not EffectDisposition.SUCCEEDED:
            return self._stop(session, context, f"receipt_{receipt.disposition}")
        observations = _models(session, "feature_observation_recorded", FeatureLaunchObservation)
        if len(observations) > 1:
            return self._stop(session, context, "feature_observation_count_exceeds_one")
        if not observations:
            observation = context.dependencies.hand.observation_for(receipt)
            session = self._runtime.append_persisted_event(
                context.store,
                session,
                event_type="feature_observation_recorded",
                payload=_json_payload(observation),
                now=context.now,
            )
        else:
            observation = observations[0]
        if not _observation_matches(execution, receipt, observation):
            return self._stop(session, context, "observation_lineage_mismatch")
        return session, observation

    def _evaluate_or_replay(
        self,
        session: AgentSession,
        execution: FeatureLaunchExecution,
        observation: FeatureLaunchObservation,
        context: FeatureLaunchRuntimeContext,
    ) -> AgentSession:
        evaluations = _models(session, "feature_evaluated", FeatureLaunchEvaluation)
        if len(evaluations) > 1:
            return self._stop(session, context, "feature_evaluation_count_exceeds_one")
        if evaluations:
            persisted_evaluation = evaluations[0]
            error = _evaluation_validation_error(
                session,
                execution,
                observation,
                persisted_evaluation,
                context.dependencies.evaluator,
            )
            if error is not None:
                return self._stop(session, context, error)
            return self._finalize_evaluation(session, persisted_evaluation, context)
        evaluated_execution = FeatureLaunchExecution(
            session, execution.task, execution.proposal, execution.admission
        )
        evaluation = context.dependencies.evaluator.evaluate(
            evaluated_execution, observation, now=context.now
        )
        session = self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="feature_evaluated",
            payload=_json_payload(evaluation),
            now=context.now,
        )
        return self._finalize_evaluation(session, evaluation, context)

    def _finalize_evaluation(
        self,
        session: AgentSession,
        evaluation: FeatureLaunchEvaluation,
        context: FeatureLaunchRuntimeContext,
    ) -> AgentSession:
        return self._runtime.finalize_persisted_session(
            context.store,
            session,
            state=(
                RuntimeState.COMPLETED
                if evaluation.state == "completed"
                else RuntimeState.INCONCLUSIVE
            ),
            reason=evaluation.state,
            now=context.now,
        )

    def _stop(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext, reason: str
    ) -> AgentSession:
        current = self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="feature_stopped",
            payload={"reason": reason},
            now=context.now,
        )
        return self._runtime.finalize_persisted_session(
            context.store,
            current,
            state=RuntimeState.INCONCLUSIVE,
            reason=reason,
            now=context.now,
        )


def _evaluation_validation_error(
    session: AgentSession,
    execution: FeatureLaunchExecution,
    observation: FeatureLaunchObservation,
    persisted: FeatureLaunchEvaluation,
    evaluator: FeatureLaunchEvaluator,
) -> str | None:
    indexed = tuple(
        (index, event)
        for index, event in enumerate(session.events)
        if event.event_type == "feature_evaluated"
    )
    if len(indexed) != 1:
        return "feature_evaluation_count_exceeds_one"
    event_index, event = indexed[0]
    if persisted.evaluated_at != event.occurred_at:
        return "feature_evaluation_timestamp_mismatch"
    prefix_execution = replace(
        execution, session=replace(session, events=session.events[:event_index])
    )
    expected = evaluator.evaluate(prefix_execution, observation, now=event.occurred_at)
    if not _evaluation_matches(persisted, expected):
        return "persisted_feature_evaluation_mismatch"
    return None


def _evaluation_matches(
    persisted: FeatureLaunchEvaluation, expected: FeatureLaunchEvaluation
) -> bool:
    return (
        persisted.evaluation_id == expected.evaluation_id
        and persisted.goal_id == expected.goal_id
        and persisted.evidence_brief_sha256 == expected.evidence_brief_sha256
        and persisted.proposal_sha256 == expected.proposal_sha256
        and persisted.observation_sha256 == expected.observation_sha256
        and persisted.process_passed == expected.process_passed
        and persisted.outcome_passed == expected.outcome_passed
        and persisted.state == expected.state
        and persisted.reasons == expected.reasons
        and persisted.evaluated_at == expected.evaluated_at
    )


def _persisted_feature_execution(
    session: AgentSession, context: FeatureLaunchRuntimeContext
) -> FeatureLaunchExecution:
    proposals = _models(session, "feature_decision_committed", DecisionProposal)
    if len(proposals) != 1:
        raise FeatureLaunchOperatorError("terminal_feature_decision_count_invalid")
    try:
        admission = context.dependencies.registry.admit(context.task, proposals[0])
    except FeatureLaunchOperatorError as error:
        raise FeatureLaunchOperatorError("terminal_feature_decision_invalid") from error
    return FeatureLaunchExecution(session, context.task, proposals[0], admission)


def _validate_terminal_feature_session(
    session: AgentSession, context: FeatureLaunchRuntimeContext
) -> None:
    _validate_terminal_feature_envelope(session, context)
    if session.state is RuntimeState.COMPLETED:
        _validate_terminal_feature_evaluation(session, context, expected_state="completed")
        return
    if session.state is RuntimeState.INCONCLUSIVE:
        evaluations = _models(session, "feature_evaluated", FeatureLaunchEvaluation)
        if not evaluations:
            if not any(event.event_type == "feature_stopped" for event in session.events):
                raise FeatureLaunchOperatorError("terminal_feature_inconclusive_trace_missing")
            return
        _validate_terminal_feature_evaluation(session, context, expected_state="inconclusive")
        return
    raise FeatureLaunchOperatorError("terminal_feature_state_invalid")


def _validate_terminal_feature_envelope(
    session: AgentSession, context: FeatureLaunchRuntimeContext
) -> None:
    _validate_terminal_feature_goal(session, context)
    _validate_terminal_feature_brief(session, context)
    _validate_terminal_feature_brief_order(session)
    _validate_terminal_feature_finalization(session)


def _validate_terminal_feature_goal(
    session: AgentSession, context: FeatureLaunchRuntimeContext
) -> None:
    goals = _models(session, "feature_goal_committed", MarketingGoal)
    if not goals:
        raise FeatureLaunchOperatorError("terminal_feature_goal_missing")
    if len(goals) != 1:
        raise FeatureLaunchOperatorError("terminal_feature_goal_count_invalid")
    if goals[0] != context.task.goal:
        raise FeatureLaunchOperatorError("persisted_goal_mismatch")


def _validate_terminal_feature_brief(
    session: AgentSession, context: FeatureLaunchRuntimeContext
) -> None:
    briefs = _models(session, "feature_launch_brief_committed", FeatureLaunchEvidenceBrief)
    if not briefs:
        raise FeatureLaunchOperatorError("terminal_feature_brief_missing")
    if len(briefs) != 1:
        raise FeatureLaunchOperatorError("terminal_feature_brief_count_invalid")
    if briefs[0] != context.task.evidence_brief:
        raise FeatureLaunchOperatorError("persisted_feature_launch_brief_mismatch")


def _validate_terminal_feature_brief_order(session: AgentSession) -> None:
    brief_position = next(
        index
        for index, event in enumerate(session.events)
        if event.event_type == "feature_launch_brief_committed"
    )
    goal_position = next(
        index
        for index, event in enumerate(session.events)
        if event.event_type == "feature_goal_committed"
    )
    if brief_position >= goal_position:
        raise FeatureLaunchOperatorError("terminal_feature_brief_order_invalid")


def _validate_terminal_feature_finalization(session: AgentSession) -> None:
    if not session.events or session.events[-1].event_type != "session_finalized":
        raise FeatureLaunchOperatorError("terminal_feature_finalization_missing")
    if session.events[-1].payload.get("state") != session.state:
        raise FeatureLaunchOperatorError("terminal_feature_state_mismatch")
    if session.state is RuntimeState.STOPPED:
        raise FeatureLaunchOperatorError("terminal_feature_stop_state_invalid")


def _validate_terminal_feature_evaluation(
    session: AgentSession,
    context: FeatureLaunchRuntimeContext,
    *,
    expected_state: Literal["completed", "inconclusive"],
) -> None:
    evaluations = _models(session, "feature_evaluated", FeatureLaunchEvaluation)
    observations = _models(session, "feature_observation_recorded", FeatureLaunchObservation)
    if len(evaluations) != 1 or len(observations) != 1:
        raise FeatureLaunchOperatorError("terminal_feature_trace_count_invalid")
    execution = _persisted_feature_execution(session, context)
    receipt = _latest_receipt(session)
    if receipt is None or receipt.disposition is not EffectDisposition.SUCCEEDED:
        raise FeatureLaunchOperatorError("terminal_feature_receipt_invalid")
    if not _observation_matches(execution, receipt, observations[0]):
        raise FeatureLaunchOperatorError("terminal_feature_observation_invalid")
    error = _evaluation_validation_error(
        session,
        execution,
        observations[0],
        evaluations[0],
        context.dependencies.evaluator,
    )
    if error is not None or evaluations[0].state != expected_state:
        raise FeatureLaunchOperatorError(error or "terminal_feature_evaluation_invalid")


def _models[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> tuple[T, ...]:
    return tuple(
        model.model_validate(event.payload)
        for event in session.events
        if event.event_type == event_type
    )


def _latest_receipt(session: AgentSession) -> ToolReceipt | None:
    for event in reversed(session.events):
        if event.event_type in {f"tool_{item}" for item in EffectDisposition}:
            return tool_receipt_from_event(event)
    return None


def _observation_matches(
    execution: FeatureLaunchExecution,
    receipt: ToolReceipt,
    observation: FeatureLaunchObservation,
) -> bool:
    return (
        observation.receipt_sha256 == receipt.receipt_sha256
        and observation.call_sha256 == receipt.call_sha256
        and observation.request_sha256 == execution.admission.call.input_sha256
        and observation.feature_packet_sha256 == contract_sha256(execution.task.feature_packet)
        and observation.evidence_brief_sha256 == contract_sha256(execution.task.evidence_brief)
        and observation.research_observation_ids == execution.proposal.research_observation_ids
        and observation.proposal_sha256 == contract_sha256(execution.proposal)
    )


def _json_payload(contract: ContractModel) -> JsonObject:
    return contract.model_dump(mode="json")


def _canonical_sha256(value: JsonObject) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode()).hexdigest()
