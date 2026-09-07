from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, ToolInvocation, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.scheduler import AgentSkillScheduler, DailySkillSchedule
from ads_booster.marketing.agent_service.skills import MarketingSkillCatalog
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.agent_service.web_search import WebSearch, search_descriptor
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.marketing.tool_adapters.descriptors import (
    research_descriptor,
    slack_delivery_descriptor,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


class ResearchThenDeliver:
    def __init__(self) -> None:
        self.calls: int = 0

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        capability = ("research.search", "deliver.slack", None)[min(self.calls, 2)]
        self.calls += 1
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop" if capability is None else "invoke_tool",
            capability_id=capability,
            tool_input=None
            if capability is None
            else (
                {"query": "app marketing"}
                if capability == "research.search"
                else {"text": "A sourced brief"}
            ),
            expected_outcome="A brief reaches Slack",
            reasoning_summary="Bounded test",
        )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake",
                model_id="fake",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


class Tool:
    def __init__(self) -> None:
        self.calls: int = 0

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        _ = descriptor
        self.calls += 1
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=contract_sha256(invocation),
            output={"receipt": "verified-test-result"},
            actual_cost_units=1,
            executor_id="fake",
        )


def test_slack_only_schedule_researches_delivers_and_never_repeats_same_day(tmp_path: Path) -> None:
    registry = ToolRegistry(
        (
            search_descriptor(now=NOW),
            slack_delivery_descriptor(installation_id="slack", observed_at=NOW, ready=True),
        )
    )
    research, slack = Tool(), Tool()
    db = tmp_path / "service.sqlite3"
    service = MarketingAgentService(
        SqliteAgentRunRepository(db),
        registry,
        ResearchThenDeliver(),
        {"research.search": research, "deliver.slack": slack},
        SqliteSessionStore(db),
    )
    assert MarketingSkillCatalog(registry).require_ready("research.daily_slack_only", now=NOW)
    schedule = DailySkillSchedule(
        "research.daily_slack_only",
        "team",
        "scheduled",
        "Asia/Seoul",
        8,
        0,
        {"query": "app marketing"},
    )
    scheduler = AgentSkillScheduler(service, (schedule,))
    first = scheduler.tick(now=NOW)
    assert first == scheduler.tick(now=NOW)
    assert research.calls == slack.calls == 1
    run = service.repository.get("team", first[0])
    assert run is not None
    assert run.state.value == "completed"


def test_search_keeps_source_urls_and_marks_snippets_as_unverified() -> None:
    owner = WebSearch(
        lambda query: [
            {"title": "Example", "href": "https://example.com/story", "body": "untrusted source"},
            {"href": "file:///private/data"},
        ]
    )
    descriptor = search_descriptor(now=NOW)
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="i1",
        run_id="r1",
        intent_sha256="a" * 64,
        step_id="s1",
        capability_snapshot_sha256="b" * 64,
        input_sha256=contract_sha256({"query": "app marketing"}),
        descriptor_sha256=contract_sha256(descriptor),
        input={"query": "app marketing"},
        idempotency_key="search1",
    )
    result = owner.execute(invocation, descriptor)
    assert result.output["sources"] == [
        {"url": "https://example.com/story", "title": "Example", "snippet": "untrusted source"}
    ]
    assert "untrusted leads" in str(result.output["caveat"])


def test_daily_slack_is_ready_without_notion_and_keeps_immutable_research_input() -> None:
    registry = ToolRegistry(
        (
            research_descriptor(installation_id="research", observed_at=NOW, ready=True),
            slack_delivery_descriptor(installation_id="slack", observed_at=NOW, ready=True),
        )
    )
    skill = MarketingSkillCatalog(registry).require_ready("research.daily_slack", now=NOW)
    assert "store.notion.daily" not in skill.required_capabilities
    immutable_request: JsonObject = {"schema_version": "trace.test-research.v1", "query": "Japan"}
    goal = skill.goal({"research_request": immutable_request})
    assert goal.context["input"] == {"research_request": immutable_request}
    assert "research.web에 그대로" in skill.procedure


def test_same_day_upgrade_reuses_frozen_v1_goal_without_reexecution(tmp_path: Path) -> None:
    provider = ResearchThenDeliver()
    provider.calls = 2  # This existing daily Run already has enough evidence to stop.
    database = tmp_path / "upgrade.sqlite3"
    service = MarketingAgentService(
        SqliteAgentRunRepository(database),
        ToolRegistry(()),
        provider,
        {},
        SqliteSessionStore(database),
    )
    run_id = "scheduled-research-daily-slack-2026-09-07"
    frozen = AgentGoal(
        objective="Legacy combined daily brief",
        success_criteria=("Legacy already completed",),
        context={
            "skill_id": "research.daily_slack",
            "skill_version": "1",
            "required_capabilities": ["research.web", "deliver.slack", "store.notion.daily"],
        },
    )
    original = service.create(
        CreateAgentRunRequest(
            run_id=run_id,
            tenant_id="team",
            goal=frozen,
            budget=AgentBudget(max_tool_calls=6, max_cost_units=100),
        ),
        now=NOW,
    )
    scheduler = AgentSkillScheduler(
        service,
        (
            DailySkillSchedule(
                "research.daily_slack",
                "team",
                "scheduled",
                "Asia/Seoul",
                8,
                0,
                {},
            ),
        ),
    )
    assert scheduler.tick(now=NOW) == (run_id,)
    assert scheduler.tick(now=NOW) == (run_id,)
    assert service.repository.get("team", run_id) == original
    assert provider.calls == 3


def test_combined_delivery_and_creative_work_have_explicit_separate_skills() -> None:
    catalog = MarketingSkillCatalog(ToolRegistry(()))
    assert (
        "store.notion.daily" in catalog.get("research.daily_slack_and_notion").required_capabilities
    )
    creative = catalog.get("creative.partial_edit")
    assert creative.required_capabilities == ("creative.prepare",)
    assert creative.goal({"preserve": ["character"], "change": ["top margin"]}).context[
        "input"
    ] == {
        "preserve": ["character"],
        "change": ["top margin"],
    }


def test_schedule_upgrade_cannot_add_automatic_approval_to_frozen_run(tmp_path: Path) -> None:
    provider = ResearchThenDeliver()
    provider.calls = 1  # Ask for Slack delivery before completion.
    slack = Tool()
    database = tmp_path / "frozen-authority.sqlite3"
    service = MarketingAgentService(
        SqliteAgentRunRepository(database),
        ToolRegistry(
            (slack_delivery_descriptor(installation_id="slack", observed_at=NOW, ready=True),)
        ),
        provider,
        {"deliver.slack": slack},
        SqliteSessionStore(database),
    )
    run_id = "scheduled-research-daily-slack-2026-09-07"
    run = service.create(
        CreateAgentRunRequest(
            run_id=run_id,
            tenant_id="team",
            goal=AgentGoal(
                objective="Existing read-only work",
                success_criteria=("Review first",),
                context={
                    "skill_id": "research.daily_slack",
                    "skill_version": "1",
                    "required_capabilities": ["research.web"],
                },
            ),
            budget=AgentBudget(max_tool_calls=6, max_cost_units=100),
        ),
        now=NOW,
    )
    assert run.state.value == "awaiting_approval"
    scheduler = AgentSkillScheduler(
        service,
        (
            DailySkillSchedule(
                "research.daily_slack",
                "team",
                "scheduled",
                "Asia/Seoul",
                8,
                0,
                {},
            ),
        ),
    )
    assert scheduler.tick(now=NOW) == (run_id,)
    assert service.repository.get("team", run_id) == run
    assert slack.calls == 0
