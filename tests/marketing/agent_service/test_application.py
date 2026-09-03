from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRunState,
    AgentStepKind,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolExecutionResult,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)
from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.runtime import SqliteSessionStore

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class AskThenStopReasoning:
    def __init__(self, *, stop: bool = False) -> None:
        self.stop: bool = stop
        self.requests: list[ReasoningRequest] = []

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        action = "stop" if self.stop or request.evidence else "request_input"
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action=action,
                expected_outcome="Resolve the launch-format uncertainty",
                reasoning_summary="Ask for evidence first"
                if action == "request_input"
                else "Enough",
            ),
        )


class InvokeThenStopReasoning:
    def __init__(self) -> None:
        self.requests: list[ReasoningRequest] = []

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        if request.evidence:
            return _reasoning_result(
                request,
                ReasoningDecision(
                    schema_version="trace.reasoning-decision.v1",
                    action="stop",
                    expected_outcome="One evidence-backed experiment exists",
                    reasoning_summary="The observation is sufficient for this bounded run",
                ),
            )
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="research.web",
                tool_input={"query": "AI lock screen changing character marketing"},
                expected_outcome="Learn whether demonstration beats a generic hook",
                reasoning_summary="Use the available observe tool; Appium is unnecessary",
            ),
        )


class ResearchAdapter:
    def __init__(self) -> None:
        self.inputs: list[JsonObject] = []

    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult:
        invocation_sha256 = contract_sha256(invocation)
        assert invocation.descriptor_sha256 == contract_sha256(descriptor)
        self.inputs.append(invocation.input)
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=invocation_sha256,
            output={"finding": "Show the changing character instead of describing it"},
            actual_cost_units=1,
            executor_id="research.fake",
        )


class FlakyReasoning:
    def __init__(self) -> None:
        self.calls: int = 0

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.calls += 1
        if self.calls == 1:
            message = "provider unavailable"
            raise RuntimeError(message)
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop",
            expected_outcome="Recovered planning completes",
            reasoning_summary="The provider is available on resume",
        )
        return _reasoning_result(request, decision)


class EffectThenStopReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop" if request.evidence else "invoke_tool",
            capability_id=None if request.evidence else "capture.appium",
            tool_input=None if request.evidence else {"screen": "lock-screen"},
            expected_outcome="Capture proves the changing character concept",
            reasoning_summary="Use the approved visual proof tool",
        )
        return _reasoning_result(request, decision)


def test_service_reasons_and_resumes_without_appium(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    first_reasoning = AskThenStopReasoning()
    first = _service(database, first_reasoning)

    waiting = first.create(_request(), now=NOW)

    assert waiting.state is AgentRunState.AWAITING_INPUT
    assert tuple(
        item.capability_id for item in first_reasoning.requests[0].capability_snapshot.descriptors
    ) == ("research.web",)
    assert [item.kind for item in first.repository.steps("trace", waiting.run_id)] == [
        AgentStepKind.OBSERVE,
        AgentStepKind.PLAN,
    ]

    restarted_reasoning = AskThenStopReasoning(stop=True)
    restarted = _service(database, restarted_reasoning)
    completed = restarted.submit_input(
        "trace",
        waiting.run_id,
        {"customer_signal": "People understand the feature after seeing it change"},
        now=NOW + timedelta(minutes=1),
    )

    assert completed.state is AgentRunState.COMPLETED
    assert restarted_reasoning.requests[0].phase == "replan"
    assert restarted.repository.get("trace", waiting.run_id) == completed
    assert [item.kind for item in restarted.repository.steps("trace", waiting.run_id)] == [
        AgentStepKind.OBSERVE,
        AgentStepKind.PLAN,
        AgentStepKind.OBSERVE,
        AgentStepKind.OBSERVE,
        AgentStepKind.REPLAN,
    ]


def test_service_executes_observe_tool_and_replans_without_appium(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    reasoning = InvokeThenStopReasoning()
    adapter = ResearchAdapter()
    service = _service(database, reasoning, research_adapter=adapter)

    completed = service.create(_request(), now=NOW)

    assert completed.state is AgentRunState.COMPLETED
    assert len(adapter.inputs) == 1
    assert [item.kind for item in service.repository.steps("trace", completed.run_id)] == [
        AgentStepKind.OBSERVE,
        AgentStepKind.PLAN,
        AgentStepKind.EXECUTE,
        AgentStepKind.VERIFY,
        AgentStepKind.EVALUATE,
        AgentStepKind.OBSERVE,
        AgentStepKind.REPLAN,
    ]
    assert reasoning.requests[1].phase == "replan"
    assert all(
        item.capability_id != "capture.appium"
        for request in reasoning.requests
        for item in request.capability_snapshot.descriptors
    )


def test_denied_tool_name_from_provider_never_reaches_adapter(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    reasoning = InvokeThenStopReasoning()
    adapter = ResearchAdapter()
    service = _service(database, reasoning, research_adapter=adapter)
    service.capability_policy = CapabilityPolicy(denied_capability_ids=("research.web",))

    with pytest.raises(ValueError, match="reasoning_selected_tool_outside_snapshot"):
        _ = service.create(_request(), now=NOW)

    assert adapter.inputs == []
    assert all(
        record.kind.value != "invocation"
        for record in service.repository.records("trace", "run-one")
    )


def test_create_retry_drives_run_after_reasoning_failure(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    reasoning = FlakyReasoning()
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(()),
        reasoning=reasoning,
        tools={},
        runtime_store=SqliteSessionStore(database),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        _ = service.create(_request(), now=NOW)

    recovered = service.create(_request(), now=NOW + timedelta(seconds=1))

    assert recovered.state is AgentRunState.COMPLETED
    assert reasoning.calls == 2


def test_effect_tool_waits_for_exact_approval_and_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()
    descriptor = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True)

    first = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )

    waiting = first.create(_request(), now=NOW)

    assert waiting.state is AgentRunState.AWAITING_APPROVAL
    assert adapter.inputs == []

    restarted = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    completed = restarted.decide_approval(
        "trace",
        waiting.run_id,
        approver_id="member-one",
        granted=True,
        now=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )

    assert completed.state is AgentRunState.COMPLETED
    assert adapter.inputs == [{"screen": "lock-screen"}]
    assert [item.kind for item in restarted.repository.steps("trace", waiting.run_id)] == [
        AgentStepKind.OBSERVE,
        AgentStepKind.PLAN,
        AgentStepKind.APPROVE,
        AgentStepKind.APPROVE,
        AgentStepKind.EXECUTE,
        AgentStepKind.VERIFY,
        AgentStepKind.EVALUATE,
        AgentStepKind.OBSERVE,
        AgentStepKind.REPLAN,
    ]


def test_rejected_effect_approval_never_calls_adapter(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()
    descriptor = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True)
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    waiting = service.create(_request(), now=NOW)

    stopped = service.decide_approval(
        "trace",
        waiting.run_id,
        approver_id="member-one",
        granted=False,
        now=NOW + timedelta(seconds=1),
    )

    assert stopped.state is AgentRunState.STOPPED
    assert adapter.inputs == []


@pytest.mark.parametrize(
    ("crash_point", "expected_state", "expected_calls"),
    [
        ("plan_committed", AgentRunState.COMPLETED, 1),
        ("execute_committed", AgentRunState.COMPLETED, 1),
        ("runtime_admitted", AgentRunState.COMPLETED, 1),
        ("execution_started", AgentRunState.AWAITING_RECONCILIATION, 0),
        ("runtime_result_persisted", AgentRunState.AWAITING_RECONCILIATION, 1),
        ("verify_committed", AgentRunState.COMPLETED, 1),
    ],
)
def test_restart_recovers_each_execution_commit_boundary_without_duplicate_effect(
    tmp_path: Path,
    crash_point: str,
    expected_state: AgentRunState,
    expected_calls: int,
) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()

    def crash(point: str) -> None:
        if point == crash_point:
            message = f"crash:{point}"
            raise RuntimeError(message)

    first = _service(database, InvokeThenStopReasoning(), research_adapter=adapter)
    first.fault_hook = crash
    with pytest.raises(RuntimeError, match=f"crash:{crash_point}"):
        _ = first.create(_request(), now=NOW)

    restarted_reasoning = InvokeThenStopReasoning()
    restarted = _service(database, restarted_reasoning, research_adapter=adapter)
    recovered = restarted.drive("trace", "run-one", now=NOW + timedelta(seconds=1))

    assert recovered.state is expected_state
    assert len(adapter.inputs) == expected_calls
    if expected_state is AgentRunState.COMPLETED:
        assert len(restarted_reasoning.requests) == 1


def test_pending_approval_rechecks_current_tool_readiness_before_mutating_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()
    ready = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True)
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((ready,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    waiting = service.create(_request(), now=NOW)
    unavailable = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=False)
    restarted = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((unavailable,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )

    with pytest.raises(ValueError, match="tool_dispatch_no_longer_available"):
        _ = restarted.decide_approval(
            "trace",
            waiting.run_id,
            approver_id="member-one",
            granted=True,
            now=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        )

    assert restarted.repository.get("trace", waiting.run_id) == waiting
    assert adapter.inputs == []


def test_restart_resumes_a_committed_exact_approval_without_second_decision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()
    descriptor = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True)

    def crash_after_approval(point: str) -> None:
        if point == "approval_committed":
            message = "crash:approval_committed"
            raise RuntimeError(message)

    first = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
        fault_hook=crash_after_approval,
    )
    waiting = first.create(_request(), now=NOW)
    with pytest.raises(RuntimeError, match="crash:approval_committed"):
        _ = first.decide_approval(
            "trace",
            waiting.run_id,
            approver_id="member-one",
            granted=True,
            now=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        )

    restarted = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=EffectThenStopReasoning(),
        tools={"capture.appium": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    completed = restarted.drive("trace", waiting.run_id, now=NOW + timedelta(seconds=2))

    assert completed.state is AgentRunState.COMPLETED
    assert adapter.inputs == [{"screen": "lock-screen"}]


def test_tenant_scoped_idempotency_blocks_same_tool_input_across_runs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()
    descriptor = _descriptor(
        "research.web",
        EffectClass.OBSERVE,
        ready=True,
        key_scope="tenant_tool_input",
    )
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((descriptor,)),
        reasoning=InvokeThenStopReasoning(),
        tools={"research.web": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    _ = service.create(_request(), now=NOW)

    duplicate = service.create(
        _request().model_copy(update={"run_id": "run-two"}),
        now=NOW + timedelta(seconds=1),
    )

    assert len(adapter.inputs) == 1
    assert duplicate.state is AgentRunState.BLOCKED
    assert duplicate.blocked_reason == "tool_idempotency_conflict"


def test_started_invocation_reconciles_even_if_tool_becomes_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-service.sqlite3"
    adapter = ResearchAdapter()

    def crash_after_start(point: str) -> None:
        if point == "execution_started":
            message = "crash:execution_started"
            raise RuntimeError(message)

    first = _service(database, InvokeThenStopReasoning(), research_adapter=adapter)
    first.fault_hook = crash_after_start
    with pytest.raises(RuntimeError, match="crash:execution_started"):
        _ = first.create(_request(), now=NOW)

    unavailable = _descriptor("research.web", EffectClass.OBSERVE, ready=False)
    restarted = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry((unavailable,)),
        reasoning=InvokeThenStopReasoning(),
        tools={"research.web": adapter},
        runtime_store=SqliteSessionStore(database),
    )
    recovered = restarted.drive("trace", "run-one", now=NOW + timedelta(seconds=1))

    assert recovered.state is AgentRunState.AWAITING_RECONCILIATION
    assert adapter.inputs == []


def _service(
    database: Path,
    reasoning: AskThenStopReasoning | InvokeThenStopReasoning,
    *,
    research_adapter: ResearchAdapter | None = None,
) -> MarketingAgentService:
    adapter = ResearchAdapter() if research_adapter is None else research_adapter
    return MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(
            (
                _descriptor("research.web", EffectClass.OBSERVE, ready=True),
                _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=False),
            )
        ),
        reasoning=reasoning,
        tools={"research.web": adapter},
        runtime_store=SqliteSessionStore(database),
    )


def _request() -> CreateAgentRunRequest:
    return CreateAgentRunRequest(
        run_id="run-one",
        tenant_id="trace",
        goal=AgentGoal(
            objective="Find a stronger AI lock-screen launch format",
            success_criteria=("Produce one evidence-backed experiment",),
        ),
        budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
    )


def _descriptor(
    capability_id: str,
    effect_class: EffectClass,
    *,
    ready: bool,
    key_scope: Literal["run_tool_input", "tenant_tool_input", "adapter_defined"] = (
        "run_tool_input"
    ),
) -> ToolDescriptor:
    schema: JsonObject = {"type": "object"}
    schema_sha256 = contract_sha256(schema)
    return ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id=capability_id,
        version="1",
        owner="test.adapter",
        installation_id="test-installation",
        input_schema=schema,
        input_schema_sha256=schema_sha256,
        output_schema=schema,
        output_schema_sha256=schema_sha256,
        config_schema=schema,
        config_schema_sha256=schema_sha256,
        receipt_schema=schema,
        receipt_schema_sha256=schema_sha256,
        credential_boundary="none",
        effect_class=effect_class,
        approval_policy=ToolApprovalPolicy(
            mode="none" if effect_class is EffectClass.OBSERVE else "required"
        ),
        cost=ToolCost(worst_case_units=1, unit="operation"),
        readiness=ToolReadiness(
            ready=ready,
            reason_code=None if ready else "worker_unavailable",
            observed_at=NOW,
            max_age_seconds=300,
        ),
        idempotency=ToolIdempotencyPolicy(key_scope=key_scope),
        reconciliation=ToolReconciliationPolicy(
            mode="none" if effect_class is EffectClass.OBSERVE else "readback",
            lookup_capability_id=None if effect_class is EffectClass.OBSERVE else "capture.status",
            terminal_dispositions=("succeeded", "failed"),
        ),
    )


def _reasoning_result(
    request: ReasoningRequest,
    decision: ReasoningDecision,
) -> ReasoningResult:
    return ReasoningResult(
        schema_version="trace.reasoning-result.v1",
        decision=decision,
        receipt=ReasoningProviderReceipt(
            schema_version="trace.reasoning-provider-receipt.v1",
            provider_id="fake.reasoning",
            model_id="fake-model",
            request_sha256=contract_sha256(request),
            output_schema_sha256="d" * 64,
            decision_sha256=contract_sha256(decision),
        ),
    )
