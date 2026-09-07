"""Composition root for the installed on-premises Marketing Agent Service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind, ToolInvocation
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore
from ads_booster.marketing.agent_service.delivery_tools import DeliveryPreparationTool
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
        delivery_tool=DeliveryPreparationTool(
            DeliveryReviewStore(
                paths.database,
                asset_verifier=CreativeAssetVerifier(
                    SqliteCreativeAssetRepository(paths.database, paths.root / "artifacts")
                ),
            ),
            repository=repository,
        ),
        research_runner=DynamicEvidenceResearchRunner(
            codex=codex,
            state_root=paths.root / "research",
            model_id=model_id,
            timeout_seconds=timeout_seconds,
        ),
    )
    adapters = configured.adapters()
    initial_descriptors = configured.descriptors(now=datetime.now(UTC))
    service = MarketingAgentService(
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

    configured.creative_capabilities = lambda invocation, now: _creative_capabilities(
        service, invocation, now
    )
    return service


def _creative_capabilities(
    service: MarketingAgentService, invocation: ToolInvocation, now: datetime
) -> frozenset[str]:
    # Resolve each invocation from canonical authority, then read the registry lazily:
    # Slack may wrap the catalog after this composition root has returned.
    run = (
        service.repository.get(invocation.tenant_id, invocation.run_id)
        if invocation.tenant_id is not None
        else None
    )
    if run is None:
        raise ValueError("creative_run_context_required")
    records = service.repository.records(run.tenant_id, run.run_id)
    calls = sum(record.kind is AgentRecordKind.INVOCATION for record in records)
    spent = 0
    for record in records:
        if record.kind is AgentRecordKind.RECEIPT:
            cost = record.payload.get("actual_cost_units")
            if not isinstance(cost, int) or isinstance(cost, bool):
                raise ValueError("tool_receipt_cost_invalid")
            spent += cost
    snapshot = service.registry.snapshot_for_plan(
        snapshot_id=f"{invocation.invocation_id}:creative-readiness",
        run_id=run.run_id,
        remaining_tool_calls=max(0, run.budget.max_tool_calls - calls),
        remaining_cost_units=max(0, run.budget.max_cost_units - spent),
        policy=service.capability_policy,
        now=now,
    )
    return frozenset(item.capability_id for item in snapshot.descriptors)


__all__ = ["InstalledServicePaths", "build_installed_marketing_agent_service"]
