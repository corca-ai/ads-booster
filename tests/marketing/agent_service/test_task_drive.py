from __future__ import annotations

from datetime import timedelta
from traceback import extract_stack
from typing import TYPE_CHECKING, override

import pytest

from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from tests.marketing.agent_service import test_application as fixtures
from tests.marketing.agent_service.completion_fixtures import ScriptedAssessor
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.task_completion import (
        SemanticAssessmentRequest,
        SemanticAssessmentResult,
    )
    from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult


def test_stop_without_verifier_is_not_completed(tmp_path: Path) -> None:
    service = fixtures.build_service(tmp_path / "drive.sqlite3", AskThenStopReasoning(stop=True))
    service.completion = None
    result = service.create(fixtures.run_request(), now=NOW)
    assert result.state is AgentRunState.BLOCKED
    task = project_task(result, service.repository.records(result.tenant_id, result.run_id))
    assert task.checkpoint.wait_reason == "verification_unavailable"


class Steps(fixtures.InvokeThenStopReasoning):
    def __init__(self) -> None:
        super().__init__()
        self.depths: list[int] = []

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.depths.append(len(extract_stack()))
        self.requests.append(request)
        calls = 12 - request.remaining_tool_calls
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="invoke_tool" if calls < 10 else "stop",
            capability_id="research.web" if calls < 10 else None,
            tool_input={"query": str(calls)} if calls < 10 else None,
            expected_outcome="Ten observations",
            reasoning_summary="Finished draft",
        )
        return fixtures.reasoning_result(request, decision)


class FreshResearch(fixtures.ResearchAdapter):
    @override
    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        result = super().execute(invocation, descriptor)
        return result.model_copy(update={"output": {"finding": invocation.input["query"]}})


def test_multi_slice_restart_keeps_ten_distinct_effect_receipts(tmp_path: Path) -> None:
    database = tmp_path / "slices.sqlite3"
    actor, adapter = Steps(), FreshResearch()
    service = fixtures.build_service(database, actor, research_adapter=adapter)
    request = fixtures.run_request()
    request = request.model_copy(
        update={"budget": request.budget.model_copy(update={"max_tool_calls": 12})}
    )
    first = service.create(request, now=NOW)
    assert first.state is AgentRunState.RUNNING
    assert len(actor.requests) == 4
    assert max(actor.depths) == min(actor.depths)
    restarted = fixtures.build_service(database, Steps(), research_adapter=adapter)
    second = restarted.drive("trace", first.run_id, now=NOW)
    assert second.state is AgentRunState.RUNNING
    final = restarted.drive("trace", first.run_id, now=NOW)
    assert final.state is AgentRunState.COMPLETED
    assert len(adapter.inputs) == 10


class RejectingAssessor(ScriptedAssessor):
    @override
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        result = super().assess(request)
        return result.model_copy(
            update={
                "requested_deliverables_supported": False,
                "obligations": tuple(
                    item.model_copy(update={"status": "unsatisfied"}) for item in result.obligations
                ),
            }
        )


def test_third_rejected_candidate_blocks_across_slice(tmp_path: Path) -> None:
    service = fixtures.build_service(tmp_path / "reject.sqlite3", AskThenStopReasoning(stop=True))
    service.completion = TaskCompletionService(service.repository, RejectingAssessor())
    first = service.create(fixtures.run_request(), now=NOW)
    assert first.state is AgentRunState.RUNNING
    final = service.drive("trace", first.run_id, now=NOW)
    assert final.state is AgentRunState.BLOCKED
    task = project_task(final, service.repository.records("trace", final.run_id))
    assert task.checkpoint.assessment_calls == 3
    assert task.checkpoint.decision_calls == 6


def test_fresh_clock_yields_before_dispatch(tmp_path: Path) -> None:
    adapter = FreshResearch()
    actor = fixtures.InvokeThenStopReasoning()
    service = fixtures.build_service(tmp_path / "clock.sqlite3", actor, research_adapter=adapter)
    service.clock = lambda: NOW - timedelta(days=int(bool(actor.requests)))
    service.monotonic_clock = lambda: 21.0 if actor.requests else 0.0
    result = service.create(fixtures.run_request(), now=NOW)
    assert result.state is AgentRunState.RUNNING
    assert adapter.inputs == []


def test_assessment_reservation_crash_cannot_refill(tmp_path: Path) -> None:
    database = tmp_path / "crash.sqlite3"
    service = fixtures.build_service(database, AskThenStopReasoning(stop=True))

    def crash(point: str) -> None:
        if point == "assessment_reserved":
            message = "assessment crashed"
            raise RuntimeError(message)

    service.fault_hook = crash
    with pytest.raises(RuntimeError, match="assessment crashed"):
        _ = service.create(fixtures.run_request(), now=NOW)
    restarted = fixtures.build_service(database, AskThenStopReasoning(stop=True))
    final = restarted.drive("trace", "run-one", now=NOW)
    assert final.state is AgentRunState.COMPLETED
    task = project_task(final, restarted.repository.records("trace", final.run_id))
    assert task.checkpoint.assessment_calls == 2
    assert task.checkpoint.decision_calls == 3


@pytest.mark.parametrize("completed", [False, True])
def test_measured_active_time_survives_restart_without_wait_time(
    tmp_path: Path, completed: bool
) -> None:
    elapsed = [0.0]

    class TimedReasoning(AskThenStopReasoning):
        @override
        def plan(self, request: ReasoningRequest) -> ReasoningResult:
            elapsed[0] += 2.0
            return super().plan(request)

    database = tmp_path / "elapsed.sqlite3"
    service = fixtures.build_service(database, TimedReasoning(stop=completed))
    service.monotonic_clock = lambda: elapsed[0]
    first = service.create(fixtures.run_request(), now=NOW)
    task = project_task(first, service.repository.records("trace", first.run_id))
    assert task.checkpoint.active_elapsed_ms == 2000
    restarted = fixtures.build_service(database, AskThenStopReasoning(stop=completed))
    final = restarted.drive("trace", first.run_id, now=NOW + timedelta(days=10))
    assert final == first
    restored = project_task(final, restarted.repository.records("trace", final.run_id))
    assert restored.checkpoint.active_elapsed_ms == 2000
