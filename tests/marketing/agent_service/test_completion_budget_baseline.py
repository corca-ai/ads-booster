from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from tests.marketing.agent_service.completion_fixtures import (
    FixtureMarketingAgentService as MarketingAgentService,
)
from tests.marketing.agent_service.test_application import (
    NOW,
    ResearchAdapter,
    research_registration,
)
from tests.marketing.agent_service.test_task_completion import (
    CompletionScript,
    completion_request,
    stop_decision,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_same_run_followup_after_restart_keeps_cumulative_tool_budget(tmp_path: Path) -> None:
    # Given one completed observation consuming the Run's entire tool budget.
    database = tmp_path / "service.db"
    first_adapter = ResearchAdapter()
    registry = ToolRegistry.from_registrations(
        (research_registration("research.web", first_adapter),), now=NOW
    )
    first_decision = ReasoningDecision(
        schema_version="trace.reasoning-decision.v1",
        action="invoke_tool",
        capability_id="research.web",
        tool_input={"query": "first experiment"},
        expected_outcome="Observe one experiment",
        reasoning_summary="Read the experiment evidence",
    )
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=registry,
        reasoning=CompletionScript((first_decision, stop_decision("Observation received."))),
        tools=registry.adapters,
        runtime_store=SqliteSessionStore(database),
    )
    request = completion_request(
        AgentGoal(objective="Research an experiment", success_criteria=("One observation",))
    ).model_copy(update={"budget": AgentBudget(max_tool_calls=1, max_cost_units=10)})
    original = service.create(request, now=NOW)
    assert original.state is AgentRunState.COMPLETED
    assert len(first_adapter.inputs) == 1
    second_adapter = ResearchAdapter()
    restarted_registry = ToolRegistry.from_registrations(
        (research_registration("research.web", second_adapter),), now=NOW
    )
    provider = CompletionScript(
        (
            first_decision.model_copy(update={"tool_input": {"query": "second experiment"}}),
            stop_decision("Another observation received."),
        )
    )
    restarted = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=restarted_registry,
        reasoning=provider,
        tools=restarted_registry.adapters,
        runtime_store=SqliteSessionStore(database),
    )

    # When an authenticated actionable followup requests another observation.
    resumed = continue_work(
        restarted,
        original.tenant_id,
        original.run_id,
        event_id="second-observation",
        actor_id="member",
        note="Research another experiment",
        action="revise",
        now=NOW,
    )

    # Then the same Run remains budget-bound and makes no second adapter call.
    assert resumed.run_id == original.run_id
    assert resumed.budget == original.budget
    assert resumed.state is AgentRunState.BLOCKED
    assert provider.requests[0].remaining_tool_calls == 0
    assert second_adapter.inputs == []
    session = restarted.runtime_store.load(original.run_id)
    assert session is not None
    assert session.tool_calls == 1
    assert session.spent_cost_units == 1
