from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.scheduler import AgentSkillScheduler, DailySkillSchedule
from ads_booster.marketing.agent_service.skills import MarketingSkillCatalog
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.agent_service.web_search import WebSearch, search_descriptor
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.marketing.tool_adapters.descriptors import slack_delivery_descriptor

if TYPE_CHECKING:
    from pathlib import Path

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
