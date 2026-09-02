"""Bounded, receipt-grounded marketing evidence research before experiment design.

This is deliberately an observe-only orchestrator. It chooses among isolated product-truth,
customer-intelligence, and market-evidence hands, then finishes with sufficient evidence or an
explicit inconclusive result. Observations never grant capabilities or modify registry policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self

from pydantic import Field, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    FeatureEvidencePacket,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.feature_launch_evidence_brief import (
    BriefEvidenceItem,
    BriefScope,
    EvidenceTrustState,
    FeatureLaunchEvidenceBrief,
    FeatureLaunchEvidenceBriefVerificationError,
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
    ToolCapability,
    ToolReceipt,
    bind_tool_invocation,
    session_trace_sha256,
    tool_receipt_from_event,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping


class EvidenceResearchOperatorError(ValueError):
    """A fail-closed research planning, capability, or evidence-lineage error."""


class ResearchScope(StrEnum):
    PRODUCT_TRUTH = "product_truth"
    CUSTOMER_INTELLIGENCE = "customer_intelligence"
    MARKET_EVIDENCE = "market_evidence"


_SCOPE_BY_ACTION_ID: dict[str, ResearchScope] = {
    "observe.product_truth": ResearchScope.PRODUCT_TRUTH,
    "observe.customer_intelligence": ResearchScope.CUSTOMER_INTELLIGENCE,
    "observe.market_evidence": ResearchScope.MARKET_EVIDENCE,
}


class ResearchState(StrEnum):
    CONTINUE = "continue"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"


_BRIEF_SCOPE_BY_RESEARCH_SCOPE: dict[ResearchScope, BriefScope] = {
    ResearchScope.PRODUCT_TRUTH: "product_truth",
    ResearchScope.CUSTOMER_INTELLIGENCE: "customer_intelligence",
    ResearchScope.MARKET_EVIDENCE: "market_evidence",
}


class EvidenceResearchGoal(ContractModel):
    schema_version: Literal["trace.evidence-research-goal.v2"]
    goal_id: AgentIdentifier
    feature_packet_id: AgentIdentifier
    feature_packet_sha256: Sha256Digest
    input_snapshot_sha256: Sha256Digest
    planner_provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    planner_model_id: Annotated[str, Field(min_length=1, max_length=240)]
    planner_protocol_sha256: Sha256Digest
    pinned_skill_registry_sha256: Sha256Digest
    required_scopes: Annotated[tuple[ResearchScope, ...], Field(min_length=1, max_length=3)]
    max_iterations: Annotated[int, Field(ge=1, le=3)]

    @model_validator(mode="after")
    def require_unique_bounded_scopes(self) -> Self:
        if len(set(self.required_scopes)) != len(self.required_scopes):
            raise ValueError("required research scopes must be unique")
        if self.max_iterations < len(self.required_scopes):
            raise ValueError("max iterations cannot be lower than required scopes")
        return self


class PlannerInvocationReceipt(ContractModel):
    """Non-secret model invocation identity committed with a planner decision."""

    schema_version: Literal["trace.planner-invocation-receipt.v1"]
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    prompt_sha256: Sha256Digest
    context_sha256: Sha256Digest
    output_schema_sha256: Sha256Digest
    planner_protocol_sha256: Sha256Digest


class ResearchDecision(ContractModel):
    schema_version: Literal["trace.evidence-research-decision.v2"]
    decision_id: AgentIdentifier
    goal_id: AgentIdentifier
    iteration: Annotated[int, Field(ge=1, le=3)]
    skill_id: Literal["evidence_research.v1"]
    skill_sha256: Sha256Digest
    action_id: Literal[
        "observe.product_truth",
        "observe.customer_intelligence",
        "observe.market_evidence",
    ]
    scope: ResearchScope
    claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(min_length=1, max_length=16)]
    research_question: Annotated[str, Field(min_length=1, max_length=1000)]
    counter_evidence_question: Annotated[str, Field(min_length=1, max_length=1000)]
    planner_receipt: PlannerInvocationReceipt

    @model_validator(mode="after")
    def require_unique_claim_ids(self) -> Self:
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("research decision claim IDs must be unique")
        return self


class ResearchObservation(ContractModel):
    schema_version: Literal["trace.evidence-research-observation.v2"]
    observation_id: AgentIdentifier
    scope: ResearchScope
    receipt_sha256: Sha256Digest
    call_sha256: Sha256Digest
    request_sha256: Sha256Digest
    feature_packet_sha256: Sha256Digest
    decision_sha256: Sha256Digest
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    evidence_summary: Annotated[str, Field(min_length=1, max_length=2000)]
    caveats: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    trust_state: EvidenceTrustState
    supported_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()
    evidence_status: Literal["sufficient", "insufficient"]
    observed_at: datetime

    @model_validator(mode="after")
    def require_valid_observation(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(
            self.observed_at
        ):
            raise ValueError("observed_at must be UTC")
        if len(set(self.supported_claim_ids)) != len(self.supported_claim_ids):
            raise ValueError("supported claim IDs must be unique")
        if self.trust_state == "unverified_model_proposal" and self.evidence_status == "sufficient":
            raise ValueError("unverified model evidence cannot be sufficient")
        return self


class ResearchObservationSummary(ContractModel):
    """Whitelisted semantic projection; it excludes raw sources, locations, and instructions."""

    scope: ResearchScope
    evidence_status: Literal["sufficient", "insufficient"]
    evidence_summary: Annotated[str, Field(min_length=1, max_length=2000)]
    caveats: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    trust_state: EvidenceTrustState
    supported_claim_ids: Annotated[tuple[AgentIdentifier, ...], Field(max_length=16)] = ()


class ResearchStepEvaluation(ContractModel):
    schema_version: Literal["trace.evidence-research-evaluation.v1"]
    evaluation_id: AgentIdentifier
    goal_id: AgentIdentifier
    completed_iterations: Annotated[int, Field(ge=1, le=3)]
    process_passed: bool
    outcome_ready: bool
    state: ResearchState
    missing_scopes: Annotated[tuple[ResearchScope, ...], Field(max_length=3)] = ()
    reasons: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    evaluated_at: datetime

    @model_validator(mode="after")
    def require_consistent_state(self) -> Self:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() != UTC.utcoffset(
            self.evaluated_at
        ):
            raise ValueError("evaluated_at must be UTC")
        if self.state is ResearchState.COMPLETED and (
            not self.process_passed or not self.outcome_ready or self.missing_scopes
        ):
            raise ValueError("completed research requires process and outcome readiness")
        if self.state is ResearchState.CONTINUE and not self.missing_scopes:
            raise ValueError("continue requires missing evidence scope")
        return self


@dataclass(frozen=True, slots=True)
class ResearchAction:
    action_id: str
    scope: ResearchScope
    capability: ToolCapability


@dataclass(frozen=True, slots=True)
class EvidenceResearchTask:
    goal: EvidenceResearchGoal
    feature_packet: FeatureEvidencePacket


@dataclass(frozen=True, slots=True)
class ResearchPlanningContext:
    goal: EvidenceResearchGoal
    product: FeaturePlanningProjection
    available_actions: tuple[ResearchAction, ...]
    observations: tuple[ResearchObservationSummary, ...]


class EvidenceResearchPlanner(Protocol):
    def propose(self, context: ResearchPlanningContext) -> ResearchDecision: ...


class EvidenceResearchHand(ToolBackend, Protocol):
    def observation_for(self, receipt: ToolReceipt) -> ResearchObservation: ...


@dataclass(frozen=True, slots=True)
class EvidenceResearchSkillRegistry:
    snapshot_sha256: str
    skill_sha256: str
    actions: tuple[ResearchAction, ...]

    def available_actions(self, observed_scopes: set[ResearchScope]) -> tuple[ResearchAction, ...]:
        return tuple(action for action in self.actions if action.scope not in observed_scopes)

    def admit(
        self,
        task: EvidenceResearchTask,
        decision: ResearchDecision,
        observed_scopes: set[ResearchScope],
    ) -> ToolAdmission:
        packet_sha256 = contract_sha256(task.feature_packet)
        action = self._action_for(decision)
        self._validate_task(task, packet_sha256)
        self._validate_decision(task, decision, observed_scopes)
        invocation = bind_tool_invocation(
            action.capability,
            call_id=f"research-{task.goal.goal_id}-{decision.decision_id}",
            idempotency_key=(
                f"research:{task.goal.goal_id}:{decision.iteration}:{decision.action_id}"
            ),
            request={
                "schema_version": "trace.evidence-research-tool-request.v1",
                "goal": task.goal.model_dump(mode="json"),
                "feature_packet_sha256": packet_sha256,
                "decision": decision.model_dump(mode="json"),
            },
        )
        return ToolAdmission(action.capability, invocation)

    def _action_for(self, decision: ResearchDecision) -> ResearchAction:
        action = next((item for item in self.actions if item.action_id == decision.action_id), None)
        expected_scope = _SCOPE_BY_ACTION_ID[decision.action_id]
        if (
            action is None
            or action.scope is not expected_scope
            or decision.scope is not expected_scope
        ):
            raise EvidenceResearchOperatorError("research_action_not_available")
        if action.capability.effect_class != "observe":
            raise EvidenceResearchOperatorError("research_action_must_be_observe")
        return action

    def _validate_task(self, task: EvidenceResearchTask, packet_sha256: str) -> None:
        if task.goal.feature_packet_id != task.feature_packet.packet_id:
            raise EvidenceResearchOperatorError("research_goal_feature_packet_id_mismatch")
        if task.goal.feature_packet_sha256 != packet_sha256:
            raise EvidenceResearchOperatorError("research_goal_feature_packet_digest_mismatch")
        if task.goal.pinned_skill_registry_sha256 != self.snapshot_sha256:
            raise EvidenceResearchOperatorError("research_goal_skill_registry_digest_mismatch")

    def _validate_decision(
        self,
        task: EvidenceResearchTask,
        decision: ResearchDecision,
        observed_scopes: set[ResearchScope],
    ) -> None:
        if decision.goal_id != task.goal.goal_id:
            raise EvidenceResearchOperatorError("research_decision_goal_mismatch")
        if decision.skill_sha256 != self.skill_sha256:
            raise EvidenceResearchOperatorError("research_decision_skill_digest_mismatch")
        if decision.planner_receipt.provider_id != task.goal.planner_provider_id:
            raise EvidenceResearchOperatorError("research_decision_provider_mismatch")
        if decision.planner_receipt.model_id != task.goal.planner_model_id:
            raise EvidenceResearchOperatorError("research_decision_model_mismatch")
        if decision.planner_receipt.planner_protocol_sha256 != task.goal.planner_protocol_sha256:
            raise EvidenceResearchOperatorError("research_decision_protocol_mismatch")
        if decision.scope not in task.goal.required_scopes:
            raise EvidenceResearchOperatorError("research_scope_not_required")
        if decision.scope in observed_scopes:
            raise EvidenceResearchOperatorError("research_scope_already_observed")
        known_claims = {claim.claim_id for claim in task.feature_packet.claims}
        if not set(decision.claim_ids).issubset(known_claims):
            raise EvidenceResearchOperatorError("research_claim_unknown")
        if decision.iteration != len(observed_scopes) + 1:
            raise EvidenceResearchOperatorError("research_iteration_mismatch")


@dataclass(frozen=True, slots=True)
class EvidenceResearchDependencies:
    planner: EvidenceResearchPlanner
    registry: EvidenceResearchSkillRegistry
    hands: Mapping[ResearchScope, EvidenceResearchHand]
    evaluator: EvidenceResearchEvaluator


@dataclass(frozen=True, slots=True)
class EvidenceResearchRuntimeContext:
    store: SessionStore
    task: EvidenceResearchTask
    dependencies: EvidenceResearchDependencies
    now: datetime


@dataclass(frozen=True, slots=True)
class ResearchExecution:
    session: AgentSession
    task: EvidenceResearchTask
    decision: ResearchDecision
    admission: ToolAdmission


class EvidenceResearchEvaluator:
    """Pure status evaluator: only receipt-bound sufficient observations close a scope."""

    def evaluate(
        self,
        session: AgentSession,
        task: EvidenceResearchTask,
        registry: EvidenceResearchSkillRegistry,
        observations: tuple[ResearchObservation, ...],
        *,
        now: datetime,
    ) -> ResearchStepEvaluation:
        missing_scopes = tuple(
            scope
            for scope in task.goal.required_scopes
            if not any(
                observation.scope is scope and observation.evidence_status == "sufficient"
                for observation in observations
            )
        )
        process_reasons = _process_reasons(session, task, registry, observations)
        process_passed = not process_reasons
        outcome_ready = not missing_scopes
        state = (
            ResearchState.COMPLETED
            if process_passed and outcome_ready
            else (
                ResearchState.INCONCLUSIVE
                if not process_passed or len(observations) >= task.goal.max_iterations
                else ResearchState.CONTINUE
            )
        )
        reasons = (
            ("receipt_grounded_evidence_complete",)
            if state is ResearchState.COMPLETED
            else (*process_reasons, *(f"missing_scope:{scope}" for scope in missing_scopes))
        )
        return ResearchStepEvaluation(
            schema_version="trace.evidence-research-evaluation.v1",
            evaluation_id=f"research-evaluation-{task.goal.goal_id}-{len(observations)}",
            goal_id=task.goal.goal_id,
            completed_iterations=len(observations),
            process_passed=process_passed,
            outcome_ready=outcome_ready,
            state=state,
            missing_scopes=missing_scopes,
            reasons=reasons or ("research_trace_invalid",),
            evaluated_at=now,
        )


class EvidenceResearchOperator:
    """Replayable bounded loop over three isolated observe-only evidence hands."""

    def __init__(self, runtime: MarketingAgentRuntime) -> None:
        self._runtime: MarketingAgentRuntime = runtime

    def run(self, session: AgentSession, context: EvidenceResearchRuntimeContext) -> AgentSession:
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            return session
        if session.state in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            _validate_terminal_research_session(session, context)
            return session
        current = self._commit_or_validate_goal(session, context)
        while current.state not in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            current = self._evaluate_or_continue(current, context)
            if current.state in {
                RuntimeState.STOPPED,
                RuntimeState.INCONCLUSIVE,
                RuntimeState.COMPLETED,
            }:
                return current
            execution = self._plan_or_replay(current, context)
            if isinstance(execution, AgentSession):
                return execution
            current = self._execute_observation(execution, context)
            if current.state is RuntimeState.AWAITING_RECONCILIATION:
                return current
        return current

    def _commit_or_validate_goal(
        self, session: AgentSession, context: EvidenceResearchRuntimeContext
    ) -> AgentSession:
        existing = _latest_model(session, "research_goal_committed", EvidenceResearchGoal)
        if existing is not None:
            if existing != context.task.goal:
                raise EvidenceResearchOperatorError("persisted_research_goal_mismatch")
            return session
        return self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="research_goal_committed",
            payload=_json_payload(context.task.goal),
            now=context.now,
        )

    def _evaluate_or_continue(
        self, session: AgentSession, context: EvidenceResearchRuntimeContext
    ) -> AgentSession:
        observations = _observations(session)
        evaluations = _evaluations(session)
        history_error = _evaluation_history_error(
            session,
            context,
            observations,
            evaluations,
        )
        if history_error is not None:
            return self._stop(session, context, history_error)
        if len(evaluations) < len(observations):
            evaluation = context.dependencies.evaluator.evaluate(
                session,
                context.task,
                context.dependencies.registry,
                observations,
                now=context.now,
            )
            session = self._runtime.append_persisted_event(
                context.store,
                session,
                event_type="research_step_evaluated",
                payload=_json_payload(evaluation),
                now=context.now,
            )
            if evaluation.state is not ResearchState.CONTINUE:
                return self._finalize(session, evaluation, context)
        elif evaluations and evaluations[-1].state is not ResearchState.CONTINUE:
            return self._finalize(session, evaluations[-1], context)
        return session

    def _plan_or_replay(
        self, session: AgentSession, context: EvidenceResearchRuntimeContext
    ) -> ResearchExecution | AgentSession:
        observations = _observations(session)
        decisions = _decisions(session)
        observed_scopes = {observation.scope for observation in observations}
        if len(decisions) > len(observations):
            decision = decisions[-1]
        else:
            available_actions = context.dependencies.registry.available_actions(observed_scopes)
            if not available_actions:
                return self._stop(session, context, "no_unobserved_research_action")
            try:
                decision = context.dependencies.planner.propose(
                    ResearchPlanningContext(
                        context.task.goal,
                        FeaturePlanningProjection.from_packet(context.task.feature_packet),
                        available_actions,
                        tuple(_summary(item) for item in observations),
                    )
                )
            except EvidenceResearchOperatorError as error:
                return self._stop(session, context, str(error))
            session = self._runtime.append_persisted_event(
                context.store,
                session,
                event_type="research_decision_committed",
                payload=_json_payload(decision),
                now=context.now,
            )
        try:
            admission = context.dependencies.registry.admit(context.task, decision, observed_scopes)
        except EvidenceResearchOperatorError as error:
            return self._stop(session, context, str(error))
        return ResearchExecution(session, context.task, decision, admission)

    def _execute_observation(
        self, execution: ResearchExecution, context: EvidenceResearchRuntimeContext
    ) -> AgentSession:
        session = execution.session
        hand = context.dependencies.hands.get(execution.decision.scope)
        if hand is None:
            return self._stop(session, context, "research_hand_missing")
        session = self._execute_hand(session, execution, hand, context)
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            return session
        return self._record_observation(session, execution, hand, context)

    def _execute_hand(
        self,
        session: AgentSession,
        execution: ResearchExecution,
        hand: EvidenceResearchHand,
        context: EvidenceResearchRuntimeContext,
    ) -> AgentSession:
        if session.pending_call is not None:
            if session.execution_started:
                return self._runtime.reconcile_interrupted_execution(
                    context.store, session, now=context.now
                )
        elif _receipt_for(session, execution.admission.call.digest) is None:
            session = self._runtime.request_persisted_tool(
                context.store, session, execution.admission, now=context.now
            )
        if session.pending_call is not None:
            session = self._runtime.execute_persisted_tool(
                context.store, session, hand, now=context.now
            )
        return session

    def _record_observation(
        self,
        session: AgentSession,
        execution: ResearchExecution,
        hand: EvidenceResearchHand,
        context: EvidenceResearchRuntimeContext,
    ) -> AgentSession:
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            return session
        receipt = _receipt_for(session, execution.admission.call.digest)
        if receipt is None:
            return self._stop(session, context, "research_receipt_missing")
        if receipt.disposition is not EffectDisposition.SUCCEEDED:
            return self._stop(session, context, f"research_receipt_{receipt.disposition}")
        if _observation_for(session, receipt.receipt_sha256) is not None:
            return session
        observation = hand.observation_for(receipt)
        if not _observation_matches(execution, receipt, observation):
            return self._stop(session, context, "research_observation_lineage_mismatch")
        return self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="research_observation_recorded",
            payload=_json_payload(observation),
            now=context.now,
        )

    def _finalize(
        self,
        session: AgentSession,
        evaluation: ResearchStepEvaluation,
        context: EvidenceResearchRuntimeContext,
    ) -> AgentSession:
        return self._runtime.finalize_persisted_session(
            context.store,
            session,
            state=(
                RuntimeState.COMPLETED
                if evaluation.state is ResearchState.COMPLETED
                else RuntimeState.INCONCLUSIVE
            ),
            reason=evaluation.state,
            now=context.now,
        )

    def _stop(
        self, session: AgentSession, context: EvidenceResearchRuntimeContext, reason: str
    ) -> AgentSession:
        session = self._runtime.append_persisted_event(
            context.store,
            session,
            event_type="research_stopped",
            payload={"reason": reason},
            now=context.now,
        )
        return self._runtime.finalize_persisted_session(
            context.store,
            session,
            state=RuntimeState.INCONCLUSIVE,
            reason=reason,
            now=context.now,
        )


def build_feature_launch_evidence_brief(
    session: AgentSession,
    context: EvidenceResearchRuntimeContext,
    *,
    brief_id: AgentIdentifier,
    now: datetime,
) -> FeatureLaunchEvidenceBrief:
    """Freeze a completed research trace for a distinct Feature Launch session.

    This function has no launch planner, hand, registry, or session-store side effect. It only
    converts an already terminal, independently validated research trace into immutable provenance.
    """
    if session.state is not RuntimeState.COMPLETED:
        raise EvidenceResearchOperatorError("research_brief_requires_completed_session")
    _validate_terminal_research_session(session, context)
    goal = _latest_model(session, "research_goal_committed", EvidenceResearchGoal)
    if goal is None:
        raise EvidenceResearchOperatorError("research_brief_goal_missing")
    evaluations = _evaluations(session)
    if len(evaluations) != len(_observations(session)) or not evaluations:
        raise EvidenceResearchOperatorError("research_brief_evaluation_missing")
    evaluation = evaluations[-1]
    if evaluation.state is not ResearchState.COMPLETED:
        raise EvidenceResearchOperatorError("research_brief_evaluation_not_completed")
    allowed_claim_ids = set(context.task.feature_packet.gate.allowed_claim_ids)
    evidence = tuple(
        BriefEvidenceItem(
            scope=_BRIEF_SCOPE_BY_RESEARCH_SCOPE[observation.scope],
            research_observation_id=observation.observation_id,
            research_observation_sha256=contract_sha256(observation),
            receipt_sha256=observation.receipt_sha256,
            call_sha256=observation.call_sha256,
            request_sha256=observation.request_sha256,
            decision_sha256=observation.decision_sha256,
            source_sha256=observation.source_sha256,
            evidence_summary=observation.evidence_summary,
            caveats=observation.caveats,
            trust_state=observation.trust_state,
            supported_allowed_claim_ids=tuple(
                claim_id
                for claim_id in observation.supported_claim_ids
                if claim_id in allowed_claim_ids
            ),
        )
        for observation in _observations(session)
    )
    return FeatureLaunchEvidenceBrief(
        schema_version="trace.feature-launch-evidence-brief.v2",
        brief_id=brief_id,
        feature_packet_id=context.task.feature_packet.packet_id,
        feature_packet_sha256=contract_sha256(context.task.feature_packet),
        research_goal_id=goal.goal_id,
        research_goal_sha256=contract_sha256(goal),
        research_registry_snapshot_sha256=goal.pinned_skill_registry_sha256,
        research_session_id=session.session_id,
        research_trace_sha256=session_trace_sha256(session),
        research_evaluation_id=evaluation.evaluation_id,
        research_evaluation_sha256=contract_sha256(evaluation),
        required_scopes=tuple(
            _BRIEF_SCOPE_BY_RESEARCH_SCOPE[scope] for scope in goal.required_scopes
        ),
        evidence=evidence,
        created_at=now,
    )


@dataclass(frozen=True, slots=True)
class ValidatedResearchEvidenceBriefVerifier:
    """Resolve and re-derive one brief from its completed Evidence Research source session.

    This local adapter is the first provenance verifier. A hosted implementation can resolve the
    same immutable source through its artifact store, but Feature Launch continues to depend only on
    the small verifier protocol rather than an Evidence Research runtime import.
    """

    context: EvidenceResearchRuntimeContext

    def verify(self, brief: FeatureLaunchEvidenceBrief) -> None:
        try:
            source_session = self.context.store.load(brief.research_session_id)
        except ValueError as error:
            raise FeatureLaunchEvidenceBriefVerificationError(
                "research_source_session_invalid"
            ) from error
        if source_session is None:
            raise FeatureLaunchEvidenceBriefVerificationError("research_source_session_not_found")
        try:
            rederived = build_feature_launch_evidence_brief(
                source_session,
                self.context,
                brief_id=brief.brief_id,
                now=brief.created_at,
            )
        except ValueError as error:
            raise FeatureLaunchEvidenceBriefVerificationError(
                "research_source_session_not_eligible"
            ) from error
        if rederived != brief:
            raise FeatureLaunchEvidenceBriefVerificationError("research_source_brief_mismatch")


def _process_reasons(
    session: AgentSession,
    task: EvidenceResearchTask,
    registry: EvidenceResearchSkillRegistry,
    observations: tuple[ResearchObservation, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    decisions = _indexed_models(session, "research_decision_committed", ResearchDecision)
    indexed_observations = _indexed_models(
        session, "research_observation_recorded", ResearchObservation
    )
    reasons.extend(_trace_count_reasons(decisions, indexed_observations, observations))
    if len({item.scope for item in observations}) != len(observations):
        reasons.append("duplicate_research_scope")
    reasons.extend(
        _observation_lineage_reasons(session, task, registry, decisions, indexed_observations)
    )
    return tuple(reasons)


def _trace_count_reasons(
    decisions: tuple[tuple[int, ResearchDecision], ...],
    indexed_observations: tuple[tuple[int, ResearchObservation], ...],
    observations: tuple[ResearchObservation, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(indexed_observations) != len(observations):
        reasons.append("research_observation_replay_mismatch")
    if len(decisions) < len(observations):
        reasons.append("research_decision_missing_for_observation")
    if len(decisions) > len(observations) + 1:
        reasons.append("research_decision_count_exceeds_observations")
    return tuple(reasons)


def _observation_lineage_reasons(
    session: AgentSession,
    task: EvidenceResearchTask,
    registry: EvidenceResearchSkillRegistry,
    decisions: tuple[tuple[int, ResearchDecision], ...],
    observations: tuple[tuple[int, ResearchObservation], ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    observed_scopes: set[ResearchScope] = set()
    for index, ((decision_position, decision), (observation_position, observation)) in enumerate(
        zip(decisions, observations, strict=False)
    ):
        if decision_position >= observation_position:
            reasons.append(f"research_decision_order_invalid:{observation.observation_id}")
        if index + 1 < len(decisions) and decisions[index + 1][0] < observation_position:
            reasons.append(f"research_decision_order_invalid:{observation.observation_id}")
        try:
            admission = registry.admit(task, decision, observed_scopes)
        except EvidenceResearchOperatorError as error:
            reasons.append(f"research_decision_invalid:{error}")
            observed_scopes.add(observation.scope)
            continue
        receipt_entry = _receipt_event_for(session, admission.call.digest)
        if receipt_entry is None:
            reasons.append(f"receipt_lineage_missing:{observation.observation_id}")
            observed_scopes.add(observation.scope)
            continue
        receipt_position, receipt = receipt_entry
        if (
            receipt.disposition is not EffectDisposition.SUCCEEDED
            or not decision_position < receipt_position < observation_position
        ):
            reasons.append(f"receipt_lineage_invalid:{observation.observation_id}")
        execution = ResearchExecution(session, task, decision, admission)
        if not _observation_matches(execution, receipt, observation):
            reasons.append(f"observation_lineage_invalid:{observation.observation_id}")
        observed_scopes.add(observation.scope)
    return tuple(reasons)


def _evaluation_history_error(
    session: AgentSession,
    context: EvidenceResearchRuntimeContext,
    observations: tuple[ResearchObservation, ...],
    evaluations: tuple[ResearchStepEvaluation, ...],
) -> str | None:
    if len(evaluations) > len(observations):
        return "research_evaluation_count_exceeds_observations"
    indexed_observations = _indexed_models(
        session, "research_observation_recorded", ResearchObservation
    )
    indexed_evaluations = _indexed_models(
        session, "research_step_evaluated", ResearchStepEvaluation
    )
    decisions = _indexed_models(session, "research_decision_committed", ResearchDecision)
    for index, (evaluation_position, persisted) in enumerate(indexed_evaluations):
        observed_prefix = tuple(
            observation
            for observation_position, observation in indexed_observations
            if observation_position < evaluation_position
        )
        if persisted.evaluated_at != session.events[evaluation_position].occurred_at:
            return "persisted_research_evaluation_timestamp_mismatch"
        if len(observed_prefix) != index + 1:
            return "research_evaluation_observation_order_invalid"
        previous_observation_position = indexed_observations[index][0]
        if any(
            previous_observation_position < decision_position < evaluation_position
            for decision_position, _ in decisions
        ):
            return "research_evaluation_decision_order_invalid"
        prefix = replace(session, events=session.events[:evaluation_position])
        expected = context.dependencies.evaluator.evaluate(
            prefix,
            context.task,
            context.dependencies.registry,
            observed_prefix,
            now=persisted.evaluated_at,
        )
        if not _evaluation_matches(persisted, expected):
            return "persisted_research_evaluation_mismatch"
    return None


def _latest_model[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> T | None:
    for event in reversed(session.events):
        if event.event_type == event_type:
            return model.model_validate(event.payload)
    return None


def _models[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> tuple[T, ...]:
    return tuple(
        model.model_validate(event.payload)
        for event in session.events
        if event.event_type == event_type
    )


def _indexed_models[T: ContractModel](
    session: AgentSession, event_type: str, model: type[T]
) -> tuple[tuple[int, T], ...]:
    return tuple(
        (index, model.model_validate(event.payload))
        for index, event in enumerate(session.events)
        if event.event_type == event_type
    )


def _decisions(session: AgentSession) -> tuple[ResearchDecision, ...]:
    return _models(session, "research_decision_committed", ResearchDecision)


def _observations(session: AgentSession) -> tuple[ResearchObservation, ...]:
    return _models(session, "research_observation_recorded", ResearchObservation)


def _evaluations(session: AgentSession) -> tuple[ResearchStepEvaluation, ...]:
    return _models(session, "research_step_evaluated", ResearchStepEvaluation)


def _receipt_for(session: AgentSession, call_sha256: str) -> ToolReceipt | None:
    receipt_entry = _receipt_event_for(session, call_sha256)
    return receipt_entry[1] if receipt_entry is not None else None


def _receipt_event_for(session: AgentSession, call_sha256: str) -> tuple[int, ToolReceipt] | None:
    for index, event in reversed(tuple(enumerate(session.events))):
        if event.event_type in {f"tool_{item}" for item in EffectDisposition}:
            receipt = tool_receipt_from_event(event)
            if receipt.call_sha256 == call_sha256:
                return index, receipt
    return None


def _observation_for(session: AgentSession, receipt_sha256: str) -> ResearchObservation | None:
    return next(
        (
            observation
            for observation in reversed(_observations(session))
            if observation.receipt_sha256 == receipt_sha256
        ),
        None,
    )


def _summary(observation: ResearchObservation) -> ResearchObservationSummary:
    return ResearchObservationSummary(
        scope=observation.scope,
        evidence_status=observation.evidence_status,
        evidence_summary=observation.evidence_summary,
        caveats=observation.caveats,
        trust_state=observation.trust_state,
        supported_claim_ids=observation.supported_claim_ids,
    )


def _observation_matches(
    execution: ResearchExecution, receipt: ToolReceipt, observation: ResearchObservation
) -> bool:
    return (
        observation.scope is execution.decision.scope
        and observation.receipt_sha256 == receipt.receipt_sha256
        and observation.call_sha256 == receipt.call_sha256
        and observation.request_sha256 == execution.admission.call.input_sha256
        and observation.feature_packet_sha256 == contract_sha256(execution.task.feature_packet)
        and observation.decision_sha256 == contract_sha256(execution.decision)
        and set(observation.supported_claim_ids).issubset(execution.decision.claim_ids)
    )


def _evaluation_matches(
    persisted: ResearchStepEvaluation, expected: ResearchStepEvaluation
) -> bool:
    return (
        persisted.evaluation_id == expected.evaluation_id
        and persisted.goal_id == expected.goal_id
        and persisted.completed_iterations == expected.completed_iterations
        and persisted.process_passed == expected.process_passed
        and persisted.outcome_ready == expected.outcome_ready
        and persisted.state is expected.state
        and persisted.missing_scopes == expected.missing_scopes
        and persisted.reasons == expected.reasons
        and persisted.evaluated_at == expected.evaluated_at
    )


def _validate_terminal_research_session(
    session: AgentSession, context: EvidenceResearchRuntimeContext
) -> None:
    persisted_goal = _latest_model(session, "research_goal_committed", EvidenceResearchGoal)
    if persisted_goal is None:
        raise EvidenceResearchOperatorError("terminal_research_goal_missing")
    if persisted_goal != context.task.goal:
        raise EvidenceResearchOperatorError("persisted_research_goal_mismatch")
    if not session.events or session.events[-1].event_type != "session_finalized":
        raise EvidenceResearchOperatorError("terminal_research_finalization_missing")
    final_payload = session.events[-1].payload
    if final_payload.get("state") != session.state:
        raise EvidenceResearchOperatorError("terminal_research_state_mismatch")
    observations = _observations(session)
    evaluations = _evaluations(session)
    if len(evaluations) != len(observations):
        raise EvidenceResearchOperatorError("terminal_research_evaluation_count_mismatch")
    history_error = _evaluation_history_error(
        session,
        context,
        observations,
        evaluations,
    )
    if history_error is not None:
        raise EvidenceResearchOperatorError(history_error)
    if session.state is RuntimeState.COMPLETED and (
        not evaluations or evaluations[-1].state is not ResearchState.COMPLETED
    ):
        raise EvidenceResearchOperatorError("terminal_research_completion_without_evidence")
    if (
        session.state is RuntimeState.INCONCLUSIVE
        and evaluations
        and (evaluations[-1].state is ResearchState.COMPLETED)
    ):
        raise EvidenceResearchOperatorError("terminal_research_inconclusive_state_mismatch")


def _json_payload(contract: ContractModel) -> JsonObject:
    return contract.model_dump(mode="json")
