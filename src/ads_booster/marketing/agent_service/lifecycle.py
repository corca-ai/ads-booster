"""Composition root for the installed on-premises Marketing Agent Service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import AgentRecordKind, ToolInvocation
from ads_booster.knowledge.batch_runtime import CurationBatchRuntime
from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.configuration import KnowledgeSettings, load_local_actor
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.indexing import KnowledgeIndexWorker
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.jobs import BoundedJobRunner
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.memory_consolidation import (
    MemoryConsolidationProcessor,
    MemoryViewDispatcher,
)
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_batch_recovery import recover_running_batches
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.runtime import KnowledgeRuntime, SqliteBatchFlusher
from ads_booster.knowledge.source_fetch import ScopedSourceFetcher
from ads_booster.knowledge.source_review_jobs import SourceReviewJobProcessor
from ads_booster.knowledge.tools import ToolHost
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore
from ads_booster.marketing.agent_service.delivery_tools import DeliveryPreparationTool
from ads_booster.marketing.agent_service.image_generation import CodexImages
from ads_booster.marketing.agent_service.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from ads_booster.marketing.agent_service.knowledge import KnowledgeServiceAdapter
from ads_booster.marketing.agent_service.knowledge_ingress import CanonicalKnowledgeIngress
from ads_booster.marketing.agent_service.knowledge_ingress_authority import (
    KnowledgeIngressAuthority,
)
from ads_booster.marketing.agent_service.managed_image_review import (
    ManagedImageReviewCatalog,
    ManagedImageReviewTool,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.dynamic_evidence_research import DynamicEvidenceResearchRunner
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from ads_booster.providers.codex_reasoning import CodexReasoningProvider

if TYPE_CHECKING:
    from pathlib import Path


_WORKSPACE_PRESENCE: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)


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


def build_installed_marketing_agent_service(  # noqa: PLR0913 - explicit installed composition inputs.
    *,
    paths: InstalledServicePaths,
    codex_executable: Path,
    model_id: str,
    timeout_seconds: float,
    integrations: AgentServiceIntegrationConfig | None = None,
    knowledge: KnowledgeServiceAdapter | None = None,
) -> MarketingAgentService:
    """Build the canonical on-premises service and configured integrations."""
    paths.prepare()
    repository = SqliteAgentRunRepository(paths.database)
    codex = CodexCli(executable=codex_executable, model=model_id)
    configured = ConfiguredAgentTools(
        config=integrations or AgentServiceIntegrationConfig(),
        images=CodexImages(codex_executable, paths.root / "images", model_id),
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
        knowledge=knowledge,
    )
    registry = ToolRegistry.from_catalog(configured, now=datetime.now(UTC))
    service = MarketingAgentService(
        repository=repository,
        registry=registry,
        reasoning=CodexReasoningProvider(
            codex=codex,
            workspace_root=paths.reasoning_workspace,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
        ),
        tools=registry.adapters,
        runtime_store=SqliteSessionStore(paths.database),
        knowledge=knowledge,
    )

    managed_review = ManagedImageReviewTool(
        repository=repository,
        assets=SqliteCreativeAssetRepository(paths.database, paths.root / "artifacts"),
        codex=codex,
    )
    review_catalog = ManagedImageReviewCatalog(managed_review)
    service.install_tool_catalog(review_catalog, now=datetime.now(UTC))
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


@dataclass(frozen=True, slots=True)
class InstalledKnowledgeRuntime:
    adapter: KnowledgeServiceAdapter
    runtime: KnowledgeRuntime


def build_installed_knowledge_runtime(
    *,
    settings: KnowledgeSettings,
    service_database: Path,
    codex: CodexCli,
    model_id: str,
) -> InstalledKnowledgeRuntime:
    root, _, _ = settings.require_enabled()
    actor = load_local_actor(settings)
    repository = SqliteKnowledgeRepository(root)
    with repository.connection() as connection:
        existing: tuple[int] | None = _WORKSPACE_PRESENCE.validate_python(
            connection.execute(
                "SELECT 1 FROM workspaces WHERE workspace_id=?", (actor.workspace_id,)
            ).fetchone()
        )
    if existing is None:
        repository.register_actor(actor, MembershipRole.ADMIN)
    ingestion = KnowledgeIngestion(repository)
    ingress = CanonicalKnowledgeIngress(
        service_database, sink=ingestion, authority=KnowledgeIngressAuthority(repository)
    )
    retriever = KnowledgeRetriever(repository)
    host = ToolHost(repository, ingestion=ingestion, retriever=retriever)
    adapter = KnowledgeServiceAdapter(
        ingress=ingress,
        repository=repository,
        host=host,
        assembler=KnowledgeContextAssembler(repository, retriever),
    )
    curation = CurationRunner(
        CurationDependencies(
            provider=CodexKnowledgeProvider(codex, root / "curation", model_id),
            tool_host=host,
            dispositions=RepositorySourceDisposition(repository),
        )
    )
    owner = KnowledgeOwner(root, f"service-{actor.actor_id}")
    owner.acquire()
    try:
        _ = recover_running_batches(repository, actor.workspace_id, owner)
    except BaseException:
        owner.release()
        raise
    processor = CanonicalJobProcessor(
        repository,
        actor,
        curation,
        MemoryConsolidationProcessor(repository, actor, ChangePublisher(repository, host.state)),
        SourceReviewJobProcessor(repository, actor, ScopedSourceFetcher()),
    )
    jobs = BoundedJobRunner(repository, processor, owner.owner_id)
    runtime = KnowledgeRuntime(
        workspace_id=actor.workspace_id,
        owner=owner,
        jobs=jobs,
        ingress=ingress,
        index=KnowledgeIndexWorker(repository),
        memory_views=MemoryViewDispatcher(repository, actor),
        batches=SqliteBatchFlusher(repository),
        curation_batches=CurationBatchRuntime(repository, actor, processor),
    )
    return InstalledKnowledgeRuntime(adapter, runtime)


__all__ = [
    "InstalledKnowledgeRuntime",
    "InstalledServicePaths",
    "build_installed_knowledge_runtime",
    "build_installed_marketing_agent_service",
]
