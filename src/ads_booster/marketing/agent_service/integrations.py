"""Configured adapters that let the canonical Agent Service reach existing owners."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation
from ads_booster.contracts.knowledge_context import knowledge_context_sha256
from ads_booster.contracts.tool_capability import ToolDescriptor
from ads_booster.marketing.agent_service.web_search import WebSearch, search_descriptor
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchResult,
)
from ads_booster.marketing.tool_adapters.compatibility import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolExecutor,
)
from ads_booster.marketing.tool_adapters.descriptors import (
    hosted_tool_install_descriptor,
    hosted_workflow_descriptor,
    notion_daily_descriptor,
    research_descriptor,
    slack_delivery_descriptor,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from ads_booster.marketing.agent_core.ports import ToolAdapter
    from ads_booster.marketing.agent_service.knowledge import KnowledgeServiceAdapter

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class HttpResponse(Protocol):
    def read(self) -> bytes: ...


class ResearchRunner(Protocol):
    def run(self, request: DynamicEvidenceResearchRequest) -> DynamicEvidenceResearchResult: ...


@dataclass(frozen=True, slots=True)
class AgentServiceIntegrationConfig:
    hosted_origin: str | None = None
    hosted_token: str | None = None
    slack_bot_token: str | None = None
    slack_channel_id: str | None = None
    notion_token: str | None = None
    notion_parent_page_id: str | None = None

    def __post_init__(self) -> None:
        """Reject partial integrations instead of silently hiding a requested tool."""
        pairs = (
            (self.hosted_origin, self.hosted_token),
            (self.slack_bot_token, self.slack_channel_id),
            (self.notion_token, self.notion_parent_page_id),
        )
        if any((left is None) != (right is None) for left, right in pairs):
            raise ValueError("agent_integration_config_incomplete")
        if self.hosted_origin is not None:
            _ = _https_origin(self.hosted_origin)


@dataclass(slots=True)
class ConfiguredAgentTools:
    """Refresh configured readiness while keeping every secret inside its adapter."""

    config: AgentServiceIntegrationConfig
    research_runner: ResearchRunner
    opener: Callable[..., HttpResponse] = urlopen
    knowledge: KnowledgeServiceAdapter | None = None

    def adapters(self) -> Mapping[str, ToolAdapter]:
        adapters: dict[str, ToolAdapter] = {
            "research.search": _delegating("research.search", "public_search", WebSearch().execute),
            "research.web": _delegating(
                "research.web", "trace.dynamic_evidence_research", self._research
            ),
        }
        if self.config.hosted_origin and self.config.hosted_token:
            adapters["catalog.hosted.install"] = _delegating(
                "catalog.hosted.install", "trace.hosted_tool_catalog", self._hosted_install
            )
            adapters["workflow.feature_launch"] = _delegating(
                "workflow.feature_launch", "trace.hosted_marketing_workflow", self._hosted
            )
        if self.config.slack_bot_token and self.config.slack_channel_id:
            adapters["deliver.slack"] = _delegating(
                "deliver.slack", "slack.chat_post_message", self._slack
            )
        if self.config.notion_token and self.config.notion_parent_page_id:
            adapters["store.notion.daily"] = _delegating(
                "store.notion.daily", "notion.pages_create", self._notion
            )
        if self.knowledge is not None:
            adapters.update(self.knowledge.adapters())
        return adapters

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        result = [
            search_descriptor(now=now),
            research_descriptor(
                installation_id="installed:research.web", observed_at=now, ready=True
            ),
        ]
        if self.config.hosted_origin and self.config.hosted_token:
            result.append(
                hosted_tool_install_descriptor(
                    installation_id="configured:hosted", observed_at=now, ready=True
                )
            )
            result.append(
                hosted_workflow_descriptor(
                    installation_id="configured:hosted", observed_at=now, ready=True
                )
            )
        if self.config.slack_bot_token and self.config.slack_channel_id:
            result.append(
                slack_delivery_descriptor(
                    installation_id="configured:slack", observed_at=now, ready=True
                )
            )
        if self.config.notion_token and self.config.notion_parent_page_id:
            result.append(
                notion_daily_descriptor(
                    installation_id="configured:notion", observed_at=now, ready=True
                )
            )
        if self.knowledge is not None:
            result.extend(self.knowledge.descriptors(now=now))
        return tuple(result)

    def _research(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        request = DynamicEvidenceResearchRequest.model_validate(invocation.input)
        result = self.research_runner.run(request)
        return DelegatedToolResult(
            disposition="no_effect",
            output=_JSON_OBJECT.validate_python(result.model_dump(mode="json")),
            actual_cost_units=result.spent_cost_units,
        )

    def _hosted(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        origin = _https_origin(self.config.hosted_origin)
        token = _required(self.config.hosted_token)
        payload = invocation.input
        transfer = None
        if self.knowledge is not None:
            research = invocation.input.get("research")
            if not isinstance(research, dict):
                raise ValueError("hosted_research_binding_required")
            account_id = research.get("account_id")
            if not isinstance(account_id, str) or not account_id:
                raise ValueError("hosted_account_binding_required")
            transfer, binding = self.knowledge.outbound_transfer(invocation, account_id)
            payload = _JSON_OBJECT.validate_python(
                {
                    **invocation.input,
                    "trusted_knowledge": {
                        "binding": binding,
                        "knowledge_context": transfer.model_dump(mode="json", by_alias=True),
                        "knowledge_context_sha256": knowledge_context_sha256(transfer),
                    },
                }
            )
        output = self._post_json(
            urljoin(f"{origin}/", "api/marketing-agent/runs"),
            payload,
            {"authorization": f"Bearer {token}", "idempotency-key": invocation.idempotency_key},
        )
        if transfer is not None:
            self._record_transfer_replicas(transfer.transfer_id, output)
        return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)

    def _record_transfer_replicas(self, transfer_id: str, output: JsonObject) -> None:
        if self.knowledge is None or output.get("knowledge_context_transfer_id") != transfer_id:
            raise ValueError("knowledge_transfer_admission_receipt_invalid")
        replicas = output.get("knowledge_context_replicas")
        if not isinstance(replicas, list) or not replicas:
            raise ValueError("knowledge_transfer_admission_receipt_invalid")
        for replica in replicas:
            if not isinstance(replica, dict):
                raise ValueError("knowledge_transfer_admission_receipt_invalid")
            system_id = replica.get("system_id")
            replica_id = replica.get("replica_id")
            if not isinstance(system_id, str) or not isinstance(replica_id, str):
                raise ValueError("knowledge_transfer_admission_receipt_invalid")
            self.knowledge.record_replica(transfer_id, system_id, replica_id)

    def _hosted_install(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        origin = _https_origin(self.config.hosted_origin)
        token = _required(self.config.hosted_token)
        output = self._post_json(
            urljoin(f"{origin}/", "api/marketing-agent/tools/install"),
            invocation.input,
            {"authorization": f"Bearer {token}", "idempotency-key": invocation.idempotency_key},
        )
        return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)

    def _slack(self, invocation: ToolInvocation, descriptor: ToolDescriptor) -> DelegatedToolResult:
        _ = descriptor
        text = invocation.input.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("slack_delivery_text_required")
        output = self._post_json(
            "https://slack.com/api/chat.postMessage",
            {"channel": _required(self.config.slack_channel_id), "text": text},
            {"authorization": f"Bearer {_required(self.config.slack_bot_token)}"},
        )
        if output.get("ok") is not True:
            raise ValueError("slack_delivery_rejected")
        return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)

    def _notion(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        title = invocation.input.get("title")
        content = invocation.input.get("content")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("notion_daily_title_required")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("notion_daily_content_required")
        output = self._post_json(
            "https://api.notion.com/v1/pages",
            {
                "parent": {"page_id": _required(self.config.notion_parent_page_id)},
                "properties": {"title": {"title": [{"text": {"content": title}}]}},
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content}}]
                        },
                    }
                ],
            },
            {
                "authorization": f"Bearer {_required(self.config.notion_token)}",
                "notion-version": "2022-06-28",
            },
        )
        return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)

    def _post_json(self, url: str, payload: JsonObject, headers: Mapping[str, str]) -> JsonObject:
        if urlsplit(url).scheme != "https":
            raise ValueError("tool_endpoint_must_be_https")
        request = Request(  # noqa: S310 - all adapter endpoints are HTTPS and operator-owned.
            url,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers={"accept": "application/json", "content-type": "application/json", **headers},
            method="POST",
        )
        try:
            response = self.opener(request, timeout=30.0)
            raw = cast("object", json.loads(response.read()))
            return _JSON_OBJECT.validate_python(raw)
        except HTTPError as error:
            raise ValueError(f"tool_endpoint_http_{error.code}") from error


def _delegating(
    capability_id: str,
    executor_id: str,
    executor: ToolExecutor,
) -> DelegatingToolAdapter:
    return DelegatingToolAdapter(
        capability_id=capability_id,
        version="1",
        executor_id=executor_id,
        executor=executor,
    )


def _required(value: str | None) -> str:
    if not value:
        raise ValueError("configured_integration_value_missing")
    return value


def _https_origin(value: str | None) -> str:
    origin = _required(value).rstrip("/")
    parts = urlsplit(origin)
    if parts.scheme != "https" or not parts.netloc or parts.path not in {"", "/"}:
        raise ValueError("hosted_origin_must_be_https_origin")
    return origin


__all__ = ["AgentServiceIntegrationConfig", "ConfiguredAgentTools", "ResearchRunner"]
