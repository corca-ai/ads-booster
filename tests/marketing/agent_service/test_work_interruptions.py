from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, override

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.agent.core.registry import ToolRegistry
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    EffectThenStopReasoning,
    InvokeThenStopReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult
    from ads_booster.transport.json_types import JsonObject


class PendingSteering:
    def __init__(self) -> None:
        self.pending: bool = False

    def read(self, tenant_id: str, run_id: str) -> JsonObject | None:
        assert tenant_id == "trace"
        assert run_id == "run-one"
        return (
            {"event_id": "signed-message-2", "actor_id": "member-1", "note": "잠깐 멈춰줘"}
            if self.pending
            else None
        )


class InterruptedReasoning(InvokeThenStopReasoning):
    def __init__(self, steering: PendingSteering) -> None:
        super().__init__()
        self.steering: PendingSteering = steering

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        result = super().plan(request)
        self.steering.pending = True
        return result


def test_steering_received_during_reasoning_prevents_first_dispatch(tmp_path: Path) -> None:
    steering = PendingSteering()
    adapter = ResearchAdapter()
    service = _service(
        tmp_path / "agent.sqlite3",
        InterruptedReasoning(steering),
        research_adapter=adapter,
    )
    service.boundary_signal = steering.read
    run = service.create(_request(), now=NOW)
    assert run.state is AgentRunState.AWAITING_INPUT
    assert adapter.inputs == []
    assert service.runtime_store.load(run.run_id) is None
    records = service.repository.records("trace", run.run_id)
    assert not any(r.kind is AgentRecordKind.RECEIPT for r in records)
    assert records[-1].payload["event_id"] == "signed-message-2"
    assert records[-1].payload["verification"] == "human_reported"
    revision = run.revision
    assert service.drive("trace", run.run_id, now=NOW).revision == revision
    assert len(service.repository.records("trace", run.run_id)) == len(records)


class InterruptedAdapter(ResearchAdapter):
    def __init__(self, steering: PendingSteering, *, response_lost: bool) -> None:
        super().__init__()
        self.steering: PendingSteering = steering
        self.response_lost: bool = response_lost

    @override
    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult:
        result = super().execute(invocation, descriptor)
        self.steering.pending = True
        if self.response_lost:
            message = "fixture response lost after dispatch"
            raise RuntimeError(message)
        return result


@pytest.mark.parametrize("response_lost", [False, True])
def test_steering_after_started_tool_preserves_receipt_or_reconciliation(
    tmp_path: Path,
    response_lost: bool,
) -> None:
    database = tmp_path / "agent.sqlite3"
    steering = PendingSteering()
    adapter = InterruptedAdapter(steering, response_lost=response_lost)
    reasoning = InvokeThenStopReasoning()
    service = _service(database, reasoning, research_adapter=adapter)
    service.boundary_signal = steering.read
    run = service.create(_request(), now=NOW)
    expected = (
        AgentRunState.AWAITING_RECONCILIATION if response_lost else AgentRunState.AWAITING_INPUT
    )
    assert run.state is expected
    assert len(adapter.inputs) == 1
    assert len(reasoning.requests) == 1
    records = service.repository.records("trace", run.run_id)
    assert sum(r.kind is AgentRecordKind.RECEIPT for r in records) == (0 if response_lost else 1)
    restarted = _service(database, AskThenStopReasoning(stop=True), research_adapter=adapter)
    restarted.boundary_signal = steering.read
    assert restarted.drive("trace", run.run_id, now=NOW).state is expected
    assert len(adapter.inputs) == 1


def test_pending_steering_stops_before_provider_and_is_consumed_once(tmp_path: Path) -> None:
    steering = PendingSteering()
    steering.pending = True
    reasoning = AskThenStopReasoning(stop=True)
    service = _service(tmp_path / "agent.sqlite3", reasoning)
    service.boundary_signal = steering.read
    run = service.create(_request(), now=NOW)
    assert run.state is AgentRunState.AWAITING_INPUT
    assert reasoning.requests == []
    resumed = service.submit_input("trace", run.run_id, {"note": "resume safely"}, now=NOW)
    assert resumed.state is AgentRunState.COMPLETED
    assert len(reasoning.requests) == 1
    records = service.repository.records("trace", run.run_id)
    assert sum(r.payload_schema_version == "trace.work-interruption.v1" for r in records) == 1


def test_steering_during_approved_production_keeps_exact_approval_receipt(tmp_path: Path) -> None:
    steering = PendingSteering()
    adapter = InterruptedAdapter(steering, response_lost=False)
    service = _service(tmp_path / "agent.sqlite3", AskThenStopReasoning())
    service.reasoning = EffectThenStopReasoning()
    service.registry = ToolRegistry(
        (_descriptor("creative.image.edit", EffectClass.LOCAL_ARTIFACT, ready=True),)
    )
    service.tools = {"creative.image.edit": adapter}
    service.boundary_signal = steering.read
    waiting = service.create(_request(), now=NOW)
    invocation = next(
        r
        for r in reversed(service.repository.records("trace", waiting.run_id))
        if r.kind is AgentRecordKind.INVOCATION
    )
    paused = service.decide_approval(
        "trace",
        waiting.run_id,
        approver_id="reviewer",
        granted=True,
        expected_invocation_sha256=contract_sha256(invocation.payload),
        expires_at=NOW + timedelta(minutes=1),
        now=NOW,
    )
    assert paused.state is AgentRunState.AWAITING_INPUT
    records = service.repository.records("trace", waiting.run_id)
    receipts = [r for r in records if r.kind is AgentRecordKind.RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["approval_sha256"] is not None
    assert receipts[0].payload["actual_cost_units"] == 1
    assert len(adapter.inputs) == 1
