from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.bootstrap.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)

NOW = datetime(2026, 9, 3, 1, 2, 3, tzinfo=UTC)

if TYPE_CHECKING:
    from urllib.request import Request

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.research.dynamic_evidence_research import (
        DynamicEvidenceResearchRequest,
        DynamicEvidenceResearchResult,
    )
    from ads_booster.transport.json_types import JsonObject


@dataclass
class Response:
    payload: dict[str, object]

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class UnusedResearchRunner:
    def run(self, request: DynamicEvidenceResearchRequest) -> DynamicEvidenceResearchResult:
        _ = request
        message = "research was not expected"
        raise AssertionError(message)


def test_configured_tools_refresh_only_integrations_with_complete_credentials() -> None:
    configured = ConfiguredAgentTools(
        config=AgentServiceIntegrationConfig(
            slack_bot_token="slack-secret",  # noqa: S106
            slack_channel_id="C123",
        ),
        research_runner=UnusedResearchRunner(),
    )

    descriptors = configured.descriptors(now=NOW)

    assert [item.capability_id for item in descriptors] == [
        "skills.list",
        "skills.read",
        "creative.prepare",
        "research.search",
        "research.web",
        "deliver.slack",
    ]
    assert set(configured.adapters()) == {
        "skills.list",
        "skills.read",
        "creative.prepare",
        "research.search",
        "research.web",
        "deliver.slack",
    }
    assert all(item.readiness.observed_at == NOW for item in descriptors)


def test_partial_integration_configuration_fails_service_startup() -> None:
    with pytest.raises(ValueError, match="agent_integration_config_incomplete"):
        _ = AgentServiceIntegrationConfig(notion_parent_page_id="page-without-token")


def test_slack_and_notion_are_real_adapters_not_catalog_references() -> None:
    urls: list[str] = []

    def opener(request: Request, *, timeout: float) -> Response:
        assert timeout == 30.0
        urls.append(request.full_url)
        if request.full_url == "https://slack.com/api/chat.postMessage":
            return Response({"ok": True, "channel": "C123", "ts": "1.2"})
        return Response({"id": "notion-page", "url": "https://notion.so/notion-page"})

    configured = ConfiguredAgentTools(
        config=AgentServiceIntegrationConfig(
            slack_bot_token="slack-secret",  # noqa: S106
            slack_channel_id="C123",
            notion_token="notion-secret",  # noqa: S106
            notion_parent_page_id="parent-page",
        ),
        research_runner=UnusedResearchRunner(),
        opener=opener,
    )
    adapters = configured.adapters()
    slack = _descriptor(configured, "deliver.slack")
    notion = _descriptor(configured, "store.notion.daily")

    slack_result = adapters[slack.capability_id].execute(
        _invocation(slack, {"text": "오늘의 마케팅 브리프"}), slack
    )
    notion_result = adapters[notion.capability_id].execute(
        _invocation(notion, {"title": "2026-09-03", "content": "브리프"}), notion
    )

    assert isinstance(slack_result, ToolExecutionResult)
    assert isinstance(notion_result, ToolExecutionResult)
    assert slack_result.output["ok"] is True
    assert notion_result.output["id"] == "notion-page"
    assert urls == [
        "https://slack.com/api/chat.postMessage",
        "https://api.notion.com/v1/pages",
    ]


def _descriptor(configured: ConfiguredAgentTools, capability_id: str) -> ToolDescriptor:
    return next(
        item for item in configured.descriptors(now=NOW) if item.capability_id == capability_id
    )


def _invocation(descriptor: ToolDescriptor, payload: JsonObject) -> ToolInvocation:
    return ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="run-1:invoke:1",
        run_id="run-1",
        step_id="run-1:step:1",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="run-1:tool:input",
        input=payload,
        input_sha256=contract_sha256(payload),
    )
