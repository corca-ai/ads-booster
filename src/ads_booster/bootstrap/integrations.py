"""Configured adapters that let the canonical Agent Service reach existing owners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Protocol
from urllib.request import urlopen

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistration
from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.creative.creative_procedures import (
    CreativeBrief,
    CreativeBriefRequest,
    build_creative_brief,
)
from ads_booster.delivery.delivery_tools import (
    DeliveryPreparationTool,
    delivery_prepare_descriptor,
)
from ads_booster.research.dynamic_evidence_research import (
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchResult,
)
from ads_booster.tools.compatibility import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolExecutor,
)
from ads_booster.tools.descriptors import (
    notion_daily_descriptor,
    research_descriptor,
    slack_delivery_descriptor,
)
from ads_booster.tools.github_issues import (
    CAPABILITY,
    GitHubIssues,
)
from ads_booster.tools.github_issues import (
    descriptor as github_descriptor,
)
from ads_booster.tools.image_generation import CodexImages
from ads_booster.tools.image_generation import descriptor as image_descriptor
from ads_booster.tools.marketing_analysis import descriptor as marketing_analysis_descriptor
from ads_booster.tools.marketing_analysis import execute as analyze_marketing
from ads_booster.tools.notion_daily import execute_notion_daily
from ads_booster.tools.skill_tools import execute as execute_skill
from ads_booster.tools.skill_tools import skill_descriptors
from ads_booster.tools.slack_delivery import execute_slack_delivery
from ads_booster.tools.web_search import WebSearch, search_descriptor
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ads_booster.agent.core.ports import ToolAdapter
    from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.tools._http_json import HttpResponse

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ResearchRunner(Protocol):
    def run(self, request: DynamicEvidenceResearchRequest) -> DynamicEvidenceResearchResult: ...


@dataclass(frozen=True, slots=True)
class AgentServiceIntegrationConfig:
    slack_bot_token: str | None = field(default=None, repr=False)
    slack_channel_id: str | None = None
    notion_token: str | None = field(default=None, repr=False)
    notion_parent_page_id: str | None = None
    github_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject partial integrations instead of silently hiding a requested tool."""
        pairs = (
            (self.slack_bot_token, self.slack_channel_id),
            (self.notion_token, self.notion_parent_page_id),
        )
        if any((left is None) != (right is None) for left, right in pairs):
            msg = "agent_integration_config_incomplete"
            raise ValueError(msg)


@dataclass(slots=True)
class ConfiguredAgentTools:
    """Refresh configured readiness while keeping every secret inside its adapter."""

    config: AgentServiceIntegrationConfig
    research_runner: ResearchRunner
    opener: Callable[..., HttpResponse] = urlopen
    delivery_tool: DeliveryPreparationTool | None = None
    creative_capabilities: Callable[[ToolInvocation, datetime], frozenset[str]] | None = None
    knowledge: KnowledgeServiceAdapter | None = None

    images: CodexImages | None = None

    def registrations(self) -> tuple[ToolRegistration, ...]:
        registrations = [
            _registration(
                "skills.list",
                "trace.skills",
                execute_skill,
                partial(_skill_descriptor, "skills.list"),
            ),
            _registration(
                "skills.read",
                "trace.skills",
                execute_skill,
                partial(_skill_descriptor, "skills.read"),
            ),
            _registration(
                "creative.prepare",
                "trace.creative_procedures",
                self._creative,
                creative_prepare_descriptor,
            ),
            _registration(
                "research.search", "public_search", WebSearch().execute, search_descriptor
            ),
            _registration(
                "marketing.analyze",
                "trace.marketing_analysis",
                analyze_marketing,
                marketing_analysis_descriptor,
            ),
            _registration(
                "research.web",
                "trace.dynamic_evidence_research",
                self._research,
                _research_descriptor,
            ),
        ]
        if self.delivery_tool is not None:
            registrations.append(
                _registration(
                    "delivery.prepare",
                    "trace.delivery_preparation",
                    self.delivery_tool.execute,
                    delivery_prepare_descriptor,
                )
            )
        if self.config.slack_bot_token and self.config.slack_channel_id:
            registrations.append(
                _registration(
                    "deliver.slack",
                    "slack.chat_post_message",
                    partial(
                        execute_slack_delivery,
                        token=_required(self.config.slack_bot_token),
                        channel_id=_required(self.config.slack_channel_id),
                        opener=self.opener,
                    ),
                    _slack_delivery_descriptor,
                )
            )
        if self.config.notion_token and self.config.notion_parent_page_id:
            registrations.append(
                _registration(
                    "store.notion.daily",
                    "notion.pages_create",
                    partial(
                        execute_notion_daily,
                        token=_required(self.config.notion_token),
                        parent_page_id=_required(self.config.notion_parent_page_id),
                        opener=self.opener,
                    ),
                    _notion_daily_descriptor,
                )
            )
        if self.config.github_token:
            registrations.append(
                _registration(
                    CAPABILITY,
                    "github.issues",
                    GitHubIssues(self.config.github_token).execute,
                    github_descriptor,
                )
            )
        if self.images is not None:
            registrations.append(
                _registration(
                    "creative.image.generate",
                    "codex.image_generation",
                    self.images.execute,
                    image_descriptor,
                )
            )
        if self.knowledge is not None:
            registrations.extend(self.knowledge.registrations())
        return tuple(registrations)

    def adapters(self) -> Mapping[str, ToolAdapter]:
        return {
            registration.capability_id: registration.adapter
            for registration in self.registrations()
            if registration.adapter is not None
        }

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return tuple(registration.descriptor(now=now) for registration in self.registrations())

    def _creative(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        request = CreativeBriefRequest.model_validate(invocation.input)
        now = datetime.now(UTC)
        # Only installed, enabled descriptors with current readiness enter the packet.
        ready = (
            self.creative_capabilities(invocation, now)
            if self.creative_capabilities
            else frozenset(
                item.capability_id
                for item in self.descriptors(now=now)
                if item.enabled
                and item.readiness.ready
                and 0
                <= (now - item.readiness.observed_at).total_seconds()
                <= item.readiness.max_age_seconds
            )
        )
        brief = build_creative_brief(
            request.task,
            request.inputs,
            preserve=request.preserve,
            change=request.change,
            locales=request.locales,
            ready_capabilities=ready,
        )
        return DelegatedToolResult(
            disposition="no_effect",
            actual_cost_units=0,
            output=_JSON_OBJECT.validate_python(brief.model_dump(mode="json")),
        )

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


def creative_prepare_descriptor(*, now: datetime) -> ToolDescriptor:
    template = research_descriptor(
        installation_id="installed:creative.prepare",
        observed_at=now,
        ready=True,
    )
    input_schema = _JSON_OBJECT.validate_python(CreativeBriefRequest.model_json_schema())
    output_schema = _JSON_OBJECT.validate_python(CreativeBrief.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": "creative.prepare",
            "owner": "ads_booster.marketing.agent_service.creative_procedures",
            "input_schema": input_schema,
            "input_schema_sha256": contract_sha256(input_schema),
            "output_schema": output_schema,
            "output_schema_sha256": contract_sha256(output_schema),
            "cost": template.cost.model_copy(update={"worst_case_units": 0, "unit": "brief"}),
            "credential_boundary": "none",
        }
    )


def _registration(
    capability_id: str,
    executor_id: str,
    executor: ToolExecutor,
    descriptor_factory: Callable[..., ToolDescriptor],
) -> ToolRegistration:
    return ToolRegistration(
        capability_id=capability_id,
        version="1",
        adapter=DelegatingToolAdapter(
            capability_id=capability_id,
            version="1",
            executor_id=executor_id,
            executor=executor,
        ),
        descriptor_factory=descriptor_factory,
    )


def _skill_descriptor(capability_id: str, *, now: datetime) -> ToolDescriptor:
    return next(item for item in skill_descriptors(now=now) if item.capability_id == capability_id)


def _research_descriptor(*, now: datetime) -> ToolDescriptor:
    return research_descriptor(
        installation_id="installed:research.web", observed_at=now, ready=True
    )


def _slack_delivery_descriptor(*, now: datetime) -> ToolDescriptor:
    return slack_delivery_descriptor(
        installation_id="configured:slack", observed_at=now, ready=True
    )


def _notion_daily_descriptor(*, now: datetime) -> ToolDescriptor:
    return notion_daily_descriptor(installation_id="configured:notion", observed_at=now, ready=True)


def _required(value: str | None) -> str:
    if not value:
        msg = "configured_integration_value_missing"
        raise ValueError(msg)
    return value


__all__ = ["AgentServiceIntegrationConfig", "ConfiguredAgentTools", "ResearchRunner"]
