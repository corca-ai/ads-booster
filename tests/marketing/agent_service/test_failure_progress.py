from __future__ import annotations

from typing import TYPE_CHECKING, override

from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import AgentRunState
from tests.marketing.agent_service.test_application import NOW, build_service, run_request
from tests.marketing.agent_service.test_task_drive import FreshResearch, Steps

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult


class KnownFailure(FreshResearch):
    @override
    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        result = super().execute(invocation, descriptor)
        return result.model_copy(
            update={
                "disposition": "failed",
                "output": {
                    "error": {"code": "fixture_unavailable"},
                    "query_echo": invocation.input["query"],
                },
            }
        )


def test_known_failure_with_varying_arguments_blocks_after_reconsideration(tmp_path: Path) -> None:
    adapter = KnownFailure()
    service = build_service(tmp_path / "failure.sqlite3", Steps(), research_adapter=adapter)
    request = run_request()
    request = request.model_copy(
        update={"budget": request.budget.model_copy(update={"max_tool_calls": 12})}
    )
    run = service.create(request, now=NOW)
    for _ in range(3):
        if run.state is not AgentRunState.RUNNING:
            break
        run = service.drive(run.tenant_id, run.run_id, now=NOW)
    assert run.state is AgentRunState.BLOCKED
    assert run.blocked_reason == "no_progress"
    assert len(adapter.inputs) == 4
    task = project_task(run, service.repository.records(run.tenant_id, run.run_id))
    assert task.checkpoint.reconsideration_used
    assert task.checkpoint.strategy_feedback is not None
