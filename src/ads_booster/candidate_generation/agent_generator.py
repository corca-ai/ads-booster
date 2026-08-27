from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunId,
    AgentRunSessionBuilder,
    AgentRunStore,
    AgentRuntime,
    ConnectorRegistry,
    DomainConnector,
    ToolPolicy,
)
from ads_booster.agent.session import AgentError
from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateFormatError,
    CandidateProviderError,
)
from ads_booster.providers.errors import ProviderError
from ads_booster.transport.json_types import JsonObject
from ads_booster.workspace import (
    CandidateContextDocument,
    CandidateCreate,
    CandidateGenerationProvenance,
    CandidateSource,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from ads_booster.agent.memory import MemoryStore
    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.context_source import CandidateContextSource
    from ads_booster.candidate_generation.models import CandidateContextBundle, CandidateDraft
    from ads_booster.config.settings import AgentSettings
    from ads_booster.tools.models import ToolContext
    from ads_booster.workspace import CandidateRecord, WorkspaceId

_CANDIDATE_TOOL: Final = "trace_propose_marketing_candidates"
_BATCH_NOT_STORED: Final = "Agent did not propose a valid candidate batch"
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _candidate_batch_id() -> str:
    return f"candidate-batch-{uuid4().hex}"


class CandidateModelSource(Protocol):
    def open(self) -> AbstractContextManager[ModelClient]: ...


class CandidateAgentPort(Protocol):
    def execute(self, connector: DomainConnector, run: AgentRun) -> AgentRun: ...


class CandidateConnector(DomainConnector, Protocol):
    def completed_drafts(self, run: AgentRun) -> tuple[CandidateDraft, ...]: ...


class CandidateConnectorFactory(Protocol):
    def __call__(self, context: CandidateContextBundle) -> CandidateConnector: ...


class CandidateGeneratorPort(Protocol):
    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
    ) -> tuple[CandidateRecord, ...]: ...


class CandidateCreator(Protocol):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord: ...


@dataclass(frozen=True, slots=True)
class CandidateAgent:
    runs: AgentRunStore
    models: CandidateModelSource
    settings: AgentSettings
    context: ToolContext
    memory_store: MemoryStore
    clock: Callable[[], float] = time.time

    def execute(self, connector: DomainConnector, run: AgentRun) -> AgentRun:
        admitted = self.runs.create(run, now=self.clock())
        with self.models.open() as client:
            sessions = AgentRunSessionBuilder(
                settings=self.settings,
                client=client,
                context=self.context,
                memory_store=self.memory_store,
            )
            return AgentRuntime(
                store=self.runs,
                connectors=ConnectorRegistry((connector,)),
                sessions=sessions,
                clock=self.clock,
            ).run(admitted.run_id)


@dataclass(frozen=True, slots=True)
class CandidateGenerator:
    store: CandidateCreator
    context_source: CandidateContextSource
    agent: CandidateAgentPort
    connector_factory: CandidateConnectorFactory
    id_factory: Callable[[], str] = _candidate_batch_id

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
    ) -> tuple[CandidateRecord, ...]:
        context = self.context_source.load()
        connector = self.connector_factory(context)
        run_id = AgentRunId(self.id_factory())
        goal_context = _JSON_OBJECT.validate_python(
            {
                "candidate_context": context.model_dump(mode="json"),
                "run_context": run_context,
                "workspace_id": workspace_id,
            }
        )
        try:
            finished = self.agent.execute(
                connector,
                AgentRun(
                    run_id=run_id,
                    connector_id=connector.manifest.connector_id,
                    connector_version=connector.manifest.version,
                    goal=AgentGoal(
                        objective="Create a grounded batch of distinct marketing candidates",
                        success_criteria=(
                            "every candidate is grounded in supplied context",
                            "country and posting slot follow the supplied context",
                            "the complete batch is ready to store for human review",
                        ),
                        context=goal_context,
                    ),
                    tool_policy=ToolPolicy(allow=(_CANDIDATE_TOOL,)),
                ),
            )
        except OAuthError as error:
            raise CandidateAuthRequiredError from error
        except ProviderError as error:
            raise CandidateProviderError(
                context_overflow=error.context_overflow,
                provider_code=error.code,
            ) from error
        except AgentError as error:
            raise CandidateProviderError(provider_code="agent_loop_failed") from error
        drafts = connector.completed_drafts(finished)
        if not drafts:
            raise CandidateFormatError(_BATCH_NOT_STORED)
        provenance = _provenance(context, finished)
        return tuple(
            self.store.create_candidate(_create(workspace_id, draft, provenance))
            for draft in drafts
        )


def _provenance(
    context: CandidateContextBundle,
    run: AgentRun,
) -> CandidateGenerationProvenance:
    """Record what this run read and which Agent run produced it.

    The run id is the join: the durable Agent run holds the whole conversation, and a
    reviewer who wants more than the summary the candidate carries can follow it there.
    """
    return CandidateGenerationProvenance(
        documents=tuple(
            CandidateContextDocument(
                relative_path=document.relative_path,
                size_bytes=len(document.text.encode("utf-8")),
            )
            for document in context.documents
        ),
        model=run.connector_id,
        instruction_chars=sum(len(document.text) for document in context.documents),
        generated_at=time.time(),
        agent_run_id=str(run.run_id),
    )


def _create(
    workspace_id: WorkspaceId,
    draft: CandidateDraft,
    provenance: CandidateGenerationProvenance,
) -> CandidateCreate:
    return CandidateCreate(
        workspace_id=workspace_id,
        source=CandidateSource.AUTO,
        country=draft.country,
        posting_slot=draft.posting_slot,
        topic=draft.topic,
        caption=draft.caption,
        hypothesis=draft.hypothesis,
        image_inputs=draft.image_inputs,
        refs_used=draft.refs_used,
        principles_applied=draft.principles_applied,
        shooting_order=draft.appium_prompt,
        persona_domain=draft.persona_domain,
        generation_provenance=provenance,
    )
