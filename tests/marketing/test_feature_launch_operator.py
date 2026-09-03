from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, override

import pytest

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    OutcomeDefinition,
    OutcomeScope,
    contract_sha256,
)
from ads_booster.marketing.feature_launch_evidence_brief import (
    BriefEvidenceItem,
    FeatureLaunchEvidenceBrief,
)
from ads_booster.marketing.feature_launch_operator import (
    AvailableAction,
    DecisionProposal,
    FeatureLaunchDependencies,
    FeatureLaunchEvaluation,
    FeatureLaunchEvaluator,
    FeatureLaunchExperimentOperator,
    FeatureLaunchHand,
    FeatureLaunchObservation,
    FeatureLaunchOperatorError,
    FeatureLaunchPlanningContext,
    FeatureLaunchRuntimeContext,
    FeatureLaunchSkillRegistry,
    FeatureLaunchTask,
    MarketingGoal,
)
from ads_booster.marketing.planning_projections import FeaturePlanningProjection
from ads_booster.marketing.runtime import (
    AgentSession,
    BoundToolInvocation,
    Budget,
    EffectDisposition,
    JsonSessionStore,
    MarketingAgentRuntime,
    RuntimeState,
    ToolCapability,
    ToolReceipt,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 1, tzinfo=UTC)
REGISTRY_SNAPSHOT = "a" * 64
SKILL_SHA256 = "b" * 64


class ScriptedPlanner:
    def __init__(self, proposal: DecisionProposal) -> None:
        self.proposal: DecisionProposal = proposal
        self.calls: list[FeatureLaunchPlanningContext] = []

    def propose(self, context: FeatureLaunchPlanningContext) -> DecisionProposal:
        self.calls.append(context)
        return self.proposal


class NoCallPlanner:
    def __init__(self) -> None:
        self.calls: int = 0

    def propose(self, context: FeatureLaunchPlanningContext) -> DecisionProposal:
        _ = context
        self.calls += 1
        message = "a committed decision must replay without planner invocation"
        raise AssertionError(message)


class TestOnlyBriefVerifier:
    """Keeps focused Feature Launch tests isolated from the research-source adapter."""

    def verify(self, brief: FeatureLaunchEvidenceBrief) -> None:
        _ = brief


@dataclass(frozen=True, slots=True)
class FakeFeatureLaunchLineage:
    packet_sha256: str
    evidence_brief_sha256: str
    research_observation_ids: tuple[str, ...]
    proposal_sha256: str


class FakeFeatureLaunchHand(FeatureLaunchHand):
    def __init__(
        self,
        *,
        lineage: FakeFeatureLaunchLineage,
        evidence_status: Literal["sufficient", "insufficient"] = "sufficient",
        counter_evidence_found: bool = False,
        mismatched_lineage: bool = False,
    ) -> None:
        self.lineage: FakeFeatureLaunchLineage = lineage
        self.evidence_status: Literal["sufficient", "insufficient"] = evidence_status
        self.counter_evidence_found: bool = counter_evidence_found
        self.mismatched_lineage: bool = mismatched_lineage
        self.calls: list[BoundToolInvocation] = []
        self.observation_calls: list[ToolReceipt] = []

    @override
    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt:
        self.calls.append(invocation)
        return ToolReceipt(
            invocation.call.call_id,
            invocation.call.digest,
            None,
            EffectDisposition.SUCCEEDED,
            1,
            "c" * 64,
        )

    @override
    def observation_for(self, receipt: ToolReceipt) -> FeatureLaunchObservation:
        self.observation_calls.append(receipt)
        invocation = self.calls[-1]
        return FeatureLaunchObservation(
            schema_version="trace.feature-launch-observation.v1",
            observation_id="observation-1",
            receipt_sha256=receipt.receipt_sha256,
            call_sha256=receipt.call_sha256,
            request_sha256=invocation.call.input_sha256,
            feature_packet_sha256=(
                "d" * 64 if self.mismatched_lineage else self.lineage.packet_sha256
            ),
            evidence_brief_sha256=self.lineage.evidence_brief_sha256,
            research_observation_ids=self.lineage.research_observation_ids,
            proposal_sha256=self.lineage.proposal_sha256,
            source_ref="fake://held-out-market-signal",
            source_sha256="e" * 64,
            evidence_status=self.evidence_status,
            counter_evidence_found=self.counter_evidence_found,
            observed_at=NOW,
        )


def _packet(*, feature_id: str = "trace.lockscreen.ai-concepts") -> FeatureEvidencePacket:
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id=f"packet-{feature_id.replace('.', '-')}",
        feature_id=feature_id,
        title="AI lock-screen concepts",
        lifecycle=FeatureLifecycle.INSTALLED_CONFIRMED,
        repository="corca-ai/Trace_iOS",
        mutable_ref="refs/heads/develop",
        resolved_commit_sha="f" * 40,
        tree_sha="e" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-ready",
                text=(
                    "Users can schedule their favorite character into changing lock-screen scenes."
                ),
                status=ClaimStatus.INSTALLED_CONFIRMED,
                evidence_ids=("installed-proof",),
            ),
            FeatureClaim(
                claim_id="claim-blocked",
                text="The feature will improve retention.",
                status=ClaimStatus.SOURCE_SUPPORTED,
                evidence_ids=("source-proof",),
            ),
        ),
        evidence=(
            EvidenceReference(
                evidence_id="installed-proof",
                kind=EvidenceKind.INSTALL_RECEIPT,
                source_uri="trace://install/verified",
                immutable_ref="install-1",
                content_sha256="1" * 64,
                result=EvidenceResult.PASSED,
                collected_at=NOW,
            ),
            EvidenceReference(
                evidence_id="source-proof",
                kind=EvidenceKind.SOURCE_DIFF,
                source_uri="https://github.com/corca-ai/Trace_iOS/commit/feature",
                immutable_ref="feature",
                content_sha256="2" * 64,
                result=EvidenceResult.OBSERVED,
                collected_at=NOW,
            ),
        ),
        gate=FeatureGate(
            publication_allowed=True,
            allowed_claim_ids=("claim-ready",),
            blocked_claim_ids=("claim-blocked",),
        ),
        observed_at=NOW,
    )


def _registry() -> FeatureLaunchSkillRegistry:
    return FeatureLaunchSkillRegistry(
        snapshot_sha256=REGISTRY_SNAPSHOT,
        skill_sha256=SKILL_SHA256,
        action=AvailableAction(
            action_id="observe.feature_launch_experiment",
            capability=ToolCapability(
                "observe.feature_launch_experiment", "3" * 64, "4" * 64, "observe", 1
            ),
        ),
    )


def _task(packet: FeatureEvidencePacket) -> FeatureLaunchTask:
    brief = FeatureLaunchEvidenceBrief(
        schema_version="trace.feature-launch-evidence-brief.v2",
        brief_id="brief-1",
        feature_packet_id=packet.packet_id,
        feature_packet_sha256=contract_sha256(packet),
        research_goal_id="research-goal-1",
        research_goal_sha256="1" * 64,
        research_registry_snapshot_sha256="2" * 64,
        research_session_id="research-session-1",
        research_trace_sha256="3" * 64,
        research_evaluation_id="research-evaluation-1",
        research_evaluation_sha256="4" * 64,
        required_scopes=("product_truth",),
        evidence=(
            BriefEvidenceItem(
                scope="product_truth",
                research_observation_id="research-observation-1",
                research_observation_sha256="5" * 64,
                receipt_sha256="6" * 64,
                call_sha256="7" * 64,
                request_sha256="8" * 64,
                decision_sha256="9" * 64,
                source_sha256="a" * 64,
                evidence_summary="Packet-bound product evidence supports the selected claim.",
                caveats=("One installed product snapshot.",),
                trust_state="packet_bound",
                supported_allowed_claim_ids=("claim-ready",),
            ),
        ),
        created_at=NOW,
    )
    return FeatureLaunchTask(
        MarketingGoal(
            schema_version="trace.marketing-goal.v1",
            goal_id="goal-1",
            feature_packet_id=packet.packet_id,
            feature_packet_sha256=contract_sha256(packet),
            outcome="feature_launch_experiment",
            pinned_skill_registry_sha256=REGISTRY_SNAPSHOT,
        ),
        packet,
        brief,
    )


def _proposal(task: FeatureLaunchTask, *, claim_id: str = "claim-ready") -> DecisionProposal:
    return DecisionProposal(
        schema_version="trace.feature-launch-decision.v1",
        proposal_id="proposal-1",
        goal_id=task.goal.goal_id,
        skill_id="feature_launch_experiment.v1",
        skill_sha256=SKILL_SHA256,
        action_id="observe.feature_launch_experiment",
        evidence_brief_sha256=contract_sha256(task.evidence_brief),
        research_observation_ids=("research-observation-1",),
        claim_ids=(claim_id,),
        control_frame="A lock screen can be useful before it is expressive.",
        challenger_frame="Your favorite character can change with your day, not sit still.",
        counter_evidence_question="Do viewers understand scheduled change without a demo?",
        falsifier="The challenger does not increase completed setup within the registered window.",
        measurement=OutcomeDefinition(
            name="setup_completed",
            scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
            window_hours=72,
        ),
    )


def _hand(
    task: FeatureLaunchTask,
    proposal: DecisionProposal,
    *,
    evidence_status: Literal["sufficient", "insufficient"] = "sufficient",
    counter_evidence_found: bool = False,
    mismatched_lineage: bool = False,
) -> FakeFeatureLaunchHand:
    return FakeFeatureLaunchHand(
        lineage=FakeFeatureLaunchLineage(
            packet_sha256=contract_sha256(task.feature_packet),
            evidence_brief_sha256=contract_sha256(task.evidence_brief),
            research_observation_ids=proposal.research_observation_ids,
            proposal_sha256=contract_sha256(proposal),
        ),
        evidence_status=evidence_status,
        counter_evidence_found=counter_evidence_found,
        mismatched_lineage=mismatched_lineage,
    )


def _context(
    store: JsonSessionStore,
    task: FeatureLaunchTask,
    planner: ScriptedPlanner | NoCallPlanner,
    hand: FakeFeatureLaunchHand,
) -> FeatureLaunchRuntimeContext:
    return FeatureLaunchRuntimeContext(
        store,
        task,
        FeatureLaunchDependencies(
            planner, _registry(), hand, FeatureLaunchEvaluator(), TestOnlyBriefVerifier()
        ),
        NOW,
    )


def _persist_brief(
    runtime: MarketingAgentRuntime,
    store: JsonSessionStore,
    session: AgentSession,
    task: FeatureLaunchTask,
) -> AgentSession:
    return runtime.append_persisted_event(
        store,
        session,
        event_type="feature_launch_brief_committed",
        payload=task.evidence_brief.model_dump(mode="json"),
        now=NOW,
    )


def _evaluation(session: AgentSession) -> FeatureLaunchEvaluation:
    event = next(event for event in session.events if event.event_type == "feature_evaluated")
    return FeatureLaunchEvaluation.model_validate(event.payload)


def test_feature_launch_operator_completes_receipt_grounded_experiment(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal)
    store = JsonSessionStore(tmp_path)

    completed = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)), _context(store, task, planner, hand)
    )

    evaluation = _evaluation(completed)
    assert completed.state is RuntimeState.COMPLETED
    assert len(planner.calls) == 1
    assert len(hand.calls) == 1
    assert evaluation.process_passed
    assert evaluation.outcome_passed
    assert evaluation.state == "completed"
    assert planner.calls[0].product == FeaturePlanningProjection.from_packet(packet)
    assert not hasattr(planner.calls[0].product, "claims")
    assert planner.calls[0].evidence.brief_sha256 == contract_sha256(task.evidence_brief)
    assert not hasattr(planner.calls[0].evidence, "source_ref")
    assert store.load("session-1") == completed


def test_committed_decision_replays_after_restart_without_calling_planner(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = _persist_brief(runtime, store, AgentSession("session-1", Budget(1, 1)), task)
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened is not None
    planner = NoCallPlanner()
    hand = _hand(task, proposal)

    completed = FeatureLaunchExperimentOperator(runtime).run(
        reopened, _context(store, task, planner, hand)
    )

    assert completed.state is RuntimeState.COMPLETED
    assert planner.calls == 0
    assert len(hand.calls) == 1


def test_existing_goal_without_a_prior_evidence_brief_fails_closed(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = runtime.append_persisted_event(
        store,
        AgentSession("session-1", Budget(1, 1)),
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    planner = NoCallPlanner()
    hand = _hand(task, proposal)

    with pytest.raises(FeatureLaunchOperatorError, match="feature_launch_brief_must_precede_goal"):
        _ = FeatureLaunchExperimentOperator(runtime).run(
            session, _context(store, task, planner, hand)
        )

    assert planner.calls == 0
    assert hand.calls == []


def test_feature_launch_rejects_claim_not_supported_by_the_selected_brief(tmp_path: Path) -> None:
    packet = _packet()
    original = _task(packet)
    task = replace(
        original,
        evidence_brief=original.evidence_brief.model_copy(
            update={
                "evidence": (
                    original.evidence_brief.evidence[0].model_copy(
                        update={"supported_allowed_claim_ids": ()}
                    ),
                )
            }
        ),
    )
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal)

    stopped = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)),
        _context(JsonSessionStore(tmp_path), task, planner, hand),
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert hand.calls == []
    assert any(
        event.payload.get("reason") == "proposal_claim_not_supported_by_brief"
        for event in stopped.events
        if event.event_type == "feature_stopped"
    )


def test_forged_persisted_observation_is_stopped_before_completion(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = _persist_brief(runtime, store, AgentSession("session-1", Budget(1, 1)), task)
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    hand = _hand(task, proposal)
    admission = _registry().admit(task, proposal)
    session = runtime.request_persisted_tool(store, session, admission, now=NOW)
    session = runtime.execute_persisted_tool(store, session, hand, now=NOW)
    forged = FeatureLaunchObservation(
        schema_version="trace.feature-launch-observation.v1",
        observation_id="forged-observation",
        receipt_sha256="c" * 64,
        call_sha256=admission.call.digest,
        request_sha256="7" * 64,
        feature_packet_sha256=contract_sha256(packet),
        evidence_brief_sha256=contract_sha256(task.evidence_brief),
        research_observation_ids=proposal.research_observation_ids,
        proposal_sha256=contract_sha256(proposal),
        source_ref="untrusted://forged-observation",
        source_sha256="e" * 64,
        evidence_status="sufficient",
        counter_evidence_found=True,
        observed_at=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_observation_recorded",
        payload=forged.model_dump(mode="json"),
        now=NOW,
    )
    planner = NoCallPlanner()

    stopped = FeatureLaunchExperimentOperator(runtime).run(
        session, _context(store, task, planner, hand)
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert planner.calls == 0
    assert len(hand.calls) == 1


def test_forged_persisted_evaluation_cannot_complete_the_feature_session(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = _persist_brief(runtime, store, AgentSession("session-1", Budget(1, 1)), task)
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    hand = _hand(task, proposal)
    admission = _registry().admit(task, proposal)
    session = runtime.request_persisted_tool(store, session, admission, now=NOW)
    session = runtime.execute_persisted_tool(store, session, hand, now=NOW)
    forged = FeatureLaunchEvaluation(
        schema_version="trace.feature-launch-evaluation.v1",
        evaluation_id="forged-evaluation",
        goal_id=task.goal.goal_id,
        evidence_brief_sha256=contract_sha256(task.evidence_brief),
        proposal_sha256=contract_sha256(proposal),
        observation_sha256="d" * 64,
        process_passed=True,
        outcome_passed=True,
        state="completed",
        reasons=("forged",),
        evaluated_at=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_evaluated",
        payload=forged.model_dump(mode="json"),
        now=NOW,
    )
    planner = NoCallPlanner()

    stopped = FeatureLaunchExperimentOperator(runtime).run(
        session, _context(store, task, planner, hand)
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert planner.calls == 0
    assert len(hand.calls) == 1


def test_multiple_persisted_decisions_are_stopped_before_the_feature_hand_runs(
    tmp_path: Path,
) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = _persist_brief(runtime, store, AgentSession("session-1", Budget(1, 1)), task)
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    planner = NoCallPlanner()
    hand = _hand(task, proposal)

    stopped = FeatureLaunchExperimentOperator(runtime).run(
        session, _context(store, task, planner, hand)
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert planner.calls == 0
    assert hand.calls == []


def test_blocked_claim_is_stopped_before_the_hand_runs(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task, claim_id="claim-blocked")
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal)

    stopped = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)),
        _context(JsonSessionStore(tmp_path), task, planner, hand),
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert hand.calls == []
    assert stopped.events[-2].event_type == "feature_stopped"


def test_insufficient_observation_is_outcome_inconclusive_but_process_passes(
    tmp_path: Path,
) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal, evidence_status="insufficient")

    result = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)),
        _context(JsonSessionStore(tmp_path), task, planner, hand),
    )

    evaluation = _evaluation(result)
    assert result.state is RuntimeState.INCONCLUSIVE
    assert evaluation.process_passed
    assert not evaluation.outcome_passed
    assert "insufficient_evidence" in evaluation.reasons


def test_counter_evidence_prevents_feature_experiment_completion(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal, counter_evidence_found=True)

    result = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)),
        _context(JsonSessionStore(tmp_path), task, planner, hand),
    )

    evaluation = _evaluation(result)
    assert result.state is RuntimeState.INCONCLUSIVE
    assert evaluation.process_passed
    assert not evaluation.outcome_passed
    assert "counter_evidence_found" in evaluation.reasons


def test_restart_after_persisted_evaluation_recovers_the_terminal_state(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal)
    runtime = MarketingAgentRuntime()
    operator = FeatureLaunchExperimentOperator(runtime)
    original_store = JsonSessionStore(tmp_path / "original")
    completed = operator.run(
        AgentSession("session-1", Budget(1, 1)), _context(original_store, task, planner, hand)
    )
    pre_final = replace(
        completed,
        state=RuntimeState.EXECUTING,
        events=completed.events[:-1],
    )
    recovery_store = JsonSessionStore(tmp_path / "recovery")
    recovery_store.save(pre_final, expected_sequence=0)
    reopened = recovery_store.load("session-1")
    assert reopened is not None
    no_call_planner = NoCallPlanner()
    no_call_hand = _hand(task, proposal)

    recovered = operator.run(
        reopened, _context(recovery_store, task, no_call_planner, no_call_hand)
    )

    assert recovered.state is RuntimeState.COMPLETED
    assert no_call_planner.calls == 0
    assert no_call_hand.calls == []


def test_terminal_feature_session_without_a_goal_is_rejected_before_any_hand_call(
    tmp_path: Path,
) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = NoCallPlanner()
    hand = _hand(task, proposal)

    with pytest.raises(FeatureLaunchOperatorError, match="terminal_feature_goal_missing"):
        _ = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
            AgentSession("session-1", Budget(1, 1), state=RuntimeState.COMPLETED),
            _context(JsonSessionStore(tmp_path), task, planner, hand),
        )

    assert planner.calls == 0
    assert hand.calls == []


def test_awaiting_reconciliation_feature_session_returns_without_reinvocation(
    tmp_path: Path,
) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    store = JsonSessionStore(tmp_path)
    runtime = MarketingAgentRuntime()
    session = _persist_brief(runtime, store, AgentSession("session-1", Budget(1, 1)), task)
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_goal_committed",
        payload=task.goal.model_dump(mode="json"),
        now=NOW,
    )
    session = runtime.append_persisted_event(
        store,
        session,
        event_type="feature_decision_committed",
        payload=proposal.model_dump(mode="json"),
        now=NOW,
    )
    hand = _hand(task, proposal)
    admission = _registry().admit(task, proposal)
    session = runtime.request_persisted_tool(store, session, admission, now=NOW)
    interrupted = runtime.start_persisted_tool_execution(store, session, now=NOW)
    planner = NoCallPlanner()
    operator = FeatureLaunchExperimentOperator(runtime)

    awaiting = operator.run(interrupted, _context(store, task, planner, hand))
    reopened = store.load("session-1")
    assert awaiting.state is RuntimeState.AWAITING_RECONCILIATION
    assert reopened is not None
    assert reopened == awaiting

    returned = operator.run(reopened, _context(store, task, planner, hand))

    assert returned == awaiting
    assert planner.calls == 0
    assert hand.calls == []


def test_non_observe_registry_capability_is_stopped_before_the_hand_runs(tmp_path: Path) -> None:
    packet = _packet()
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal)
    unsafe_registry = replace(
        _registry(),
        action=AvailableAction(
            action_id="observe.feature_launch_experiment",
            capability=ToolCapability("local.render", "6" * 64, "4" * 64, "local_artifact", 1),
        ),
    )
    context = FeatureLaunchRuntimeContext(
        JsonSessionStore(tmp_path),
        task,
        FeatureLaunchDependencies(
            planner, unsafe_registry, hand, FeatureLaunchEvaluator(), TestOnlyBriefVerifier()
        ),
        NOW,
    )

    stopped = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)), context
    )

    assert stopped.state is RuntimeState.INCONCLUSIVE
    assert hand.calls == []


def test_held_out_feature_packet_requires_observation_lineage_before_completion(
    tmp_path: Path,
) -> None:
    packet = _packet(feature_id="trace.focus-mode.scenes")
    task = _task(packet)
    proposal = _proposal(task)
    planner = ScriptedPlanner(proposal)
    hand = _hand(task, proposal, mismatched_lineage=True)

    result = FeatureLaunchExperimentOperator(MarketingAgentRuntime()).run(
        AgentSession("session-1", Budget(1, 1)),
        _context(JsonSessionStore(tmp_path), task, planner, hand),
    )

    assert result.state is RuntimeState.INCONCLUSIVE
    assert len(hand.calls) == 1
    assert all(event.event_type != "feature_evaluated" for event in result.events)
