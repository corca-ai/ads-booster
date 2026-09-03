"""Composition root for the installed on-premises Marketing Agent Service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.dynamic_evidence_research import DynamicEvidenceResearchRunner
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstalledServicePaths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "agent-service.sqlite3"

    @property
    def reasoning_workspace(self) -> Path:
        return self.root / "reasoning"

    def prepare(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)


def build_installed_marketing_agent_service(
    *,
    paths: InstalledServicePaths,
    codex_executable: Path,
    model_id: str,
    timeout_seconds: float,
    integrations: AgentServiceIntegrationConfig | None = None,
) -> MarketingAgentService:
    """Build the canonical service independently from every Mac/Appium lifecycle."""
    paths.prepare()
    repository = SqliteAgentRunRepository(paths.database)
    codex = CodexCli(executable=codex_executable, model=model_id)
    configured = ConfiguredAgentTools(
        config=integrations or AgentServiceIntegrationConfig(),
        research_runner=DynamicEvidenceResearchRunner(
            codex=codex,
            state_root=paths.root / "research",
            model_id=model_id,
            timeout_seconds=timeout_seconds,
        ),
    )
    adapters = configured.adapters()
    initial_descriptors = configured.descriptors(now=datetime.now(UTC))
    return MarketingAgentService(
        repository=repository,
        registry=ToolRegistry(initial_descriptors, provider=configured),
        reasoning=CodexReasoningProvider(
            codex=codex,
            workspace_root=paths.reasoning_workspace,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
        ),
        tools=adapters,
        runtime_store=SqliteSessionStore(paths.database),
    )


__all__ = ["InstalledServicePaths", "build_installed_marketing_agent_service"]
