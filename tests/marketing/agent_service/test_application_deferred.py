# pyright: reportPrivateUsage=false
"""Real canonical/runtime SQLite boundaries with an explicitly fake async adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    AgentRunState,
    ToolExecutionDeferred,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.contracts.tool_capability import EffectClass, ToolDescriptor, ToolExecutionResult
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.agent.runtime import SqliteSessionStore

from .test_application import NOW, _descriptor, _reasoning_result, _request

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class SimulatedCrash(BaseException):
    pass


class AsyncAdapter:
    def __init__(self) -> None:
        self.calls: list[ToolInvocation] = []

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionDeferred:
        assert descriptor.capability_id == "capture.test"
        self.calls.append(invocation)
        return ToolExecutionDeferred(
            schema_version="trace.tool-deferred.v1",
            invocation_sha256=contract_sha256(invocation),
            operation_id="op-" + invocation.invocation_id,
            executor_id="fake-worker",
        )


class Reasoning:
    def __init__(self, target_calls: int = 1) -> None:
        self.calls: int = 0
        self.target_calls: int = target_calls

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.calls += 1
        outputs = [e for e in request.evidence if e.get("capability_id") == "capture.test"]
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop" if len(outputs) >= self.target_calls else "invoke_tool",
            capability_id=None if len(outputs) >= self.target_calls else "capture.test",
            tool_input=None if len(outputs) >= self.target_calls else {"index": len(outputs)},
            reasoning_summary="Bounded asynchronous capture",
            expected_outcome="One terminal receipt per operation",
        )
        return _reasoning_result(request, decision)


def build(tmp_path: Path, adapter: AsyncAdapter, reasoning: Reasoning) -> MarketingAgentService:
    db = tmp_path / "service.db"
    schema: JsonObject = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    }
    descriptor = _descriptor("capture.test", EffectClass.LOCAL_ARTIFACT, ready=True).model_copy(
        update={"output_schema": schema, "output_schema_sha256": contract_sha256(schema)}
    )
    descriptor = descriptor.model_copy(
        update={"readiness": descriptor.readiness.model_copy(update={"max_age_seconds": 86400})}
    )
    return MarketingAgentService(
        repository=SqliteAgentRunRepository(db),
        registry=ToolRegistry((descriptor,)),
        reasoning=reasoning,
        tools={"capture.test": adapter},
        runtime_store=SqliteSessionStore(db),
    )


def latest(service: MarketingAgentService) -> ToolInvocation:
    return ToolInvocation.model_validate(
        next(
            r.payload
            for r in reversed(service.repository.records("trace", "run-one"))
            if r.kind is AgentRecordKind.INVOCATION
        )
    )


def approve(service: MarketingAgentService) -> None:
    _ = service.decide_approval(
        "trace",
        "run-one",
        approver_id="reviewer",
        granted=True,
        expected_invocation_sha256=contract_sha256(latest(service)),
        now=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )


def result(invocation: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(
        schema_version="trace.tool-execution-result.v1",
        invocation_sha256=contract_sha256(invocation),
        executor_id="fake-worker",
        disposition="succeeded",
        output={"value": "verified"},
        actual_cost_units=1,
    )


def start(
    tmp_path: Path, *, target_calls: int = 1
) -> tuple[MarketingAgentService, AsyncAdapter, Reasoning, ToolInvocation]:
    adapter, reasoning = AsyncAdapter(), Reasoning(target_calls)
    service = build(tmp_path, adapter, reasoning)
    _ = service.create(_request(), now=NOW)
    approve(service)
    run = service.repository.get("trace", "run-one")
    assert run is not None
    assert run.state is AgentRunState.AWAITING_TOOL
    return service, adapter, reasoning, latest(service)


def complete(service: MarketingAgentService, invocation: ToolInvocation) -> AgentRunState:
    return service.complete_deferred(
        "trace",
        "run-one",
        operation_id="op-" + invocation.invocation_id,
        result=result(invocation),
        now=NOW + timedelta(hours=1),
    ).state


def test_restart_preserves_reserved_budget_and_consumed_approval_can_expire(tmp_path: Path) -> None:
    service, adapter, _, invocation = start(tmp_path)
    session = service.runtime_store.load("run-one")
    assert session is not None
    assert session.reserved_cost_units == 1
    assert session.spent_cost_units == 0
    restarted = build(tmp_path, adapter, Reasoning())
    assert restarted.drive("trace", "run-one", now=NOW).state is AgentRunState.AWAITING_TOOL
    assert len(adapter.calls) == 1
    assert complete(restarted, invocation) is AgentRunState.COMPLETED
    session = restarted.runtime_store.load("run-one")
    assert session is not None
    assert session.reserved_cost_units == 0
    assert session.spent_cost_units == 1


@pytest.mark.parametrize(
    "fault", ["operation", "executor", "invocation", "schema", "cost", "tenant", "run"]
)
def test_wrong_terminal_results_do_not_mutate_wait_or_release_reservation(
    tmp_path: Path, fault: str
) -> None:
    service, _, _, invocation = start(tmp_path)
    terminal = result(invocation)
    operation, tenant, run = "op-" + invocation.invocation_id, "trace", "run-one"
    if fault == "operation":
        operation = "wrong"
    elif fault == "executor":
        terminal = terminal.model_copy(update={"executor_id": "other-worker"})
    elif fault == "invocation":
        terminal = terminal.model_copy(update={"invocation_sha256": "f" * 64})
    elif fault == "schema":
        terminal = terminal.model_copy(update={"output": {"unexpected": True}})
    elif fault == "cost":
        terminal = terminal.model_copy(update={"actual_cost_units": 2})
    elif fault == "tenant":
        tenant = "other"
    else:
        run = "other"
    before = service.repository.get("trace", "run-one")
    with pytest.raises(ValueError, match=r"deferred_|schema_invalid|agent_run_not_found"):
        _ = service.complete_deferred(tenant, run, operation_id=operation, result=terminal, now=NOW)
    assert service.repository.get("trace", "run-one") == before
    session = service.runtime_store.load("run-one")
    assert session is not None
    assert session.reserved_cost_units == 1


@pytest.mark.parametrize(
    "boundary", ["deferred_completion_committed", "deferred_runtime_settled", "verify_committed"]
)
def test_completion_crash_boundaries_recover_without_dispatch_or_double_cost(
    tmp_path: Path, boundary: str
) -> None:
    service, adapter, _, invocation = start(tmp_path)

    def crash(point: str) -> None:
        if point == boundary:
            raise SimulatedCrash

    service.fault_hook = crash
    with pytest.raises(SimulatedCrash):
        _ = complete(service, invocation)
    restarted = build(tmp_path, adapter, Reasoning())
    assert (
        restarted.drive("trace", "run-one", now=NOW + timedelta(hours=1)).state
        is AgentRunState.COMPLETED
    )
    assert len(adapter.calls) == 1
    session = restarted.runtime_store.load("run-one")
    assert session is not None
    assert session.spent_cost_units == 1
    assert session.reserved_cost_units == 0
    assert (
        sum(
            r.kind is AgentRecordKind.RECEIPT
            for r in restarted.repository.records("trace", "run-one")
        )
        == 1
    )


@pytest.mark.parametrize(
    "boundary", ["deferred_ack_committed", "runtime_result_persisted", "deferred_wait_committed"]
)
def test_acknowledgement_crash_never_reenters_adapter(tmp_path: Path, boundary: str) -> None:
    adapter = AsyncAdapter()
    service = build(tmp_path, adapter, Reasoning())
    _ = service.create(_request(), now=NOW)

    def crash(point: str) -> None:
        if point == boundary:
            raise SimulatedCrash

    service.fault_hook = crash
    with pytest.raises(SimulatedCrash):
        approve(service)
    restarted = build(tmp_path, adapter, Reasoning())
    assert restarted.drive("trace", "run-one", now=NOW).state is AgentRunState.AWAITING_TOOL
    assert complete(restarted, latest(restarted)) is AgentRunState.COMPLETED
    assert len(adapter.calls) == 1


def test_duplicate_old_completion_does_not_change_subsequent_approval_or_replan(
    tmp_path: Path,
) -> None:
    service, adapter, reasoning, invocation = start(tmp_path, target_calls=2)
    assert complete(service, invocation) is AgentRunState.AWAITING_APPROVAL
    before = service.repository.get("trace", "run-one")
    calls = reasoning.calls
    assert complete(service, invocation) is AgentRunState.AWAITING_APPROVAL
    assert service.repository.get("trace", "run-one") == before
    assert reasoning.calls == calls
    assert len(adapter.calls) == 1


def test_pending_pause_records_result_then_waits_in_same_run(tmp_path: Path) -> None:
    service, adapter, reasoning, invocation = start(tmp_path)
    run = continue_work(
        service,
        "trace",
        "run-one",
        event_id="pause",
        actor_id="reviewer",
        action="pause",
        note="잠깐 멈춰줘",
        now=NOW,
    )
    assert run.state is AgentRunState.AWAITING_TOOL
    calls = reasoning.calls
    assert complete(service, invocation) is AgentRunState.AWAITING_INPUT
    assert reasoning.calls == calls
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("crash_after_runtime", [False, True])
def test_uncertainty_keeps_cost_reserved_and_late_owner_result_can_resolve(
    tmp_path: Path, crash_after_runtime: bool
) -> None:
    service, adapter, _, invocation = start(tmp_path)

    def crash(point: str) -> None:
        if point == "deferred_uncertainty_persisted":
            raise SimulatedCrash

    if crash_after_runtime:
        service.fault_hook = crash
        with pytest.raises(SimulatedCrash):
            _ = service.mark_deferred_uncertain(
                "trace", "run-one", operation_id="op-" + invocation.invocation_id, now=NOW
            )
    else:
        assert (
            service.mark_deferred_uncertain(
                "trace", "run-one", operation_id="op-" + invocation.invocation_id, now=NOW
            ).state
            is AgentRunState.AWAITING_RECONCILIATION
        )
    restarted = build(tmp_path, adapter, Reasoning())
    assert (
        restarted.drive("trace", "run-one", now=NOW).state is AgentRunState.AWAITING_RECONCILIATION
    )
    session = restarted.runtime_store.load("run-one")
    assert session is not None
    assert session.reserved_cost_units == 1
    assert complete(restarted, invocation) is AgentRunState.COMPLETED
    assert len(adapter.calls) == 1


def test_conflicting_duplicate_result_cannot_replace_terminal_evidence(tmp_path: Path) -> None:
    service, _, _, invocation = start(tmp_path)
    assert complete(service, invocation) is AgentRunState.COMPLETED
    before = service.repository.get("trace", "run-one")
    changed = result(invocation).model_copy(update={"output": {"value": "different"}})
    with pytest.raises(ValueError, match="deferred_completion_conflict"):
        _ = service.complete_deferred(
            "trace",
            "run-one",
            operation_id="op-" + invocation.invocation_id,
            result=changed,
            now=NOW + timedelta(hours=1),
        )
    assert service.repository.get("trace", "run-one") == before


def test_concurrent_identical_completions_settle_and_replan_once(tmp_path: Path) -> None:
    service, adapter, reasoning, invocation = start(tmp_path)
    before = reasoning.calls
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete, service, invocation) for _ in range(2)]
        assert all(future.result() is AgentRunState.COMPLETED for future in futures)
    assert reasoning.calls == before + 1
    assert len(adapter.calls) == 1
    assert (
        sum(
            r.kind is AgentRecordKind.RECEIPT
            for r in service.repository.records("trace", "run-one")
        )
        == 1
    )
