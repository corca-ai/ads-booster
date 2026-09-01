"""A receipt-grounded first vertical for feature-launch experiment design.

The operator has one observe-only skill. It proves that a planner can select a constrained marketing
action, replay a committed decision without another model call, and finish only from a receipt-bound
observation. It deliberately cannot publish, spend, contact customers, or mutate a control plane.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
        return self


class FeatureLaunchEvaluation(ContractModel):
    schema_version: Literal["trace.feature-launch-evaluation.v1"]
    evaluation_id: AgentIdentifier
    goal_id: AgentIdentifier
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


@dataclass(frozen=True, slots=True)
class FeatureLaunchPlanningContext:
    goal: MarketingGoal
    feature_packet: FeatureEvidencePacket
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
        if self.action.capability.effect_class != "observe":
            raise FeatureLaunchOperatorError("feature_launch_action_must_be_observe")
        if task.goal.feature_packet_id != task.feature_packet.packet_id:
            raise FeatureLaunchOperatorError("goal_feature_packet_id_mismatch")
        if task.goal.feature_packet_sha256 != packet_sha256:
            raise FeatureLaunchOperatorError("goal_feature_packet_digest_mismatch")
        if task.goal.pinned_skill_registry_sha256 != self.snapshot_sha256:
            raise FeatureLaunchOperatorError("goal_skill_registry_digest_mismatch")
        if proposal.goal_id != task.goal.goal_id:
            raise FeatureLaunchOperatorError("proposal_goal_mismatch")
        if proposal.skill_sha256 != self.skill_sha256:
            raise FeatureLaunchOperatorError("proposal_skill_digest_mismatch")
        if proposal.action_id != self.action.action_id:
            raise FeatureLaunchOperatorError("proposal_action_not_available")
        if not set(proposal.claim_ids).issubset(task.feature_packet.gate.allowed_claim_ids):
            raise FeatureLaunchOperatorError("proposal_claim_not_allowed")
        input_sha256 = _canonical_sha256(
            {
                "goal": task.goal.model_dump(mode="json"),
                "feature_packet_sha256": packet_sha256,
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


@dataclass(frozen=True, slots=True)
class FeatureLaunchDependencies:
    planner: FeatureLaunchPlanner
    registry: FeatureLaunchSkillRegistry
    hand: FeatureLaunchHand
    evaluator: FeatureLaunchEvaluator


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
        if observation.proposal_sha256 != contract_sha256(execution.proposal):
            reasons.append("observation_proposal_mismatch")
        if observation.evidence_status != "sufficient":
            reasons.append("insufficient_evidence")
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
        if session.state in {
            RuntimeState.AWAITING_RECONCILIATION,
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
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
        current = self._commit_or_validate_goal(session, context)
        if current.state in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            return current
        current, proposal = self._plan_or_replay(current, context)
        try:
            admission = context.dependencies.registry.admit(context.task, proposal)
        except FeatureLaunchOperatorError as error:
            return self._stop(current, context, str(error))
        return FeatureLaunchExecution(current, context.task, proposal, admission)

    def _commit_or_validate_goal(
        self, session: AgentSession, context: FeatureLaunchRuntimeContext
    ) -> AgentSession:
        existing = _latest_model(session, "feature_goal_committed", MarketingGoal)
        if existing is not None:
            if existing != context.task.goal:
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
        committed = _latest_model(session, "feature_decision_committed", DecisionProposal)
        if committed is not None:
            return session, committed
        proposal = context.dependencies.planner.propose(
            FeatureLaunchPlanningContext(
                context.task.goal,
                context.task.feature_packet,
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
        receipt = _latest_receipt(session)
        if receipt is None:
            return self._stop(session, context, "missing_runtime_receipt")
        if receipt.disposition is not EffectDisposition.SUCCEEDED:
            return self._stop(session, context, f"receipt_{receipt.disposition}")
        observation = _latest_observation(session)
        if observation is None:
            observation = context.dependencies.hand.observation_for(receipt)
            if not _observation_matches(execution, receipt, observation):
                return self._stop(session, context, "observation_lineage_mismatch")
            session = self._runtime.append_persisted_event(
                context.store,
                session,
                event_type="feature_observation_recorded",
                payload=_json_payload(observation),
                now=context.now,
            )
        persisted_evaluation = _latest_evaluation(session)
        if persisted_evaluation is not None:
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


def _latest_model[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> T | None:
    for event in reversed(session.events):
        if event.event_type == event_type:
            return model.model_validate(event.payload)
    return None


def _latest_receipt(session: AgentSession) -> ToolReceipt | None:
    for event in reversed(session.events):
        if event.event_type in {f"tool_{item}" for item in EffectDisposition}:
            return tool_receipt_from_event(event)
    return None


def _latest_observation(session: AgentSession) -> FeatureLaunchObservation | None:
    return _latest_model(session, "feature_observation_recorded", FeatureLaunchObservation)


def _latest_evaluation(session: AgentSession) -> FeatureLaunchEvaluation | None:
    return _latest_model(session, "feature_evaluated", FeatureLaunchEvaluation)


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
        and observation.proposal_sha256 == contract_sha256(execution.proposal)
    )


def _json_payload(contract: ContractModel) -> JsonObject:
    return contract.model_dump(mode="json")


def _canonical_sha256(value: JsonObject) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode()).hexdigest()
