from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.agent.memory import JsonlMemoryStore
from ads_booster.agent.runs import AgentRunStore
from ads_booster.auth.codex import CodexOAuth
from ads_booster.auth.store import AuthStore
from ads_booster.candidate_generation.agent_generator import (
    CandidateAgent,
    CandidateGenerator,
)
from ads_booster.candidate_generation.agent_image_runner import (
    CandidateImageRunner,
    CandidateImageStore,
)
from ads_booster.candidate_generation.context_source import (
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    default_context_directory,
)
from ads_booster.candidate_generation.instruction import SYSTEM_INSTRUCTION
from ads_booster.candidate_generation.script_generator import (
    CandidateWriter,
    ScriptCandidateGenerator,
)
from ads_booster.connectors.trace.v1.candidates import TraceCandidateConnector
from ads_booster.connectors.trace.v1.composition import (
    TraceConnectorApproval,
    build_trace_v1_runner,
)
from ads_booster.marketing.native_capture import SimctlDeviceResolver
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.tools.models import ToolContext
from ads_booster.transport.http import create_http_client

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.agent_generator import CandidateCreator
    from ads_booster.config.settings import AgentSettings
    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.results import TraceRunResult


@dataclass(frozen=True, slots=True)
class ProductionCandidateModels:
    """Opens one provider client per generation run, using the host OAuth credential.

    `instructions` overrides the client's default system prompt. The Agent-kernel path
    leaves it unset and keeps the tool-using agent prompt; the single-call script engine
    supplies its own, because a run with no tools has nothing to say about tool policy.
    """

    settings: AgentSettings
    instructions: str | None = None

    @contextmanager
    def open(self) -> Generator[ModelClient]:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            client = CodexResponsesClient(
                http=http,
                oauth=CodexOAuth(http=http, store=AuthStore.default()),
                model=self.settings.model,
                reasoning_effort=self.settings.reasoning_effort,
            )
            if self.instructions is not None:
                client.instructions = self.instructions
            yield client


def build_script_candidate_generator(
    settings: AgentSettings,
    store: CandidateWriter,
) -> ScriptCandidateGenerator:
    """Compose the single-call generation engine from settings and the context directory."""
    return ScriptCandidateGenerator(
        store=store,
        models=ProductionCandidateModels(settings, instructions=SYSTEM_INSTRUCTION),
        context_source=CandidateContextSource(
            default_context_directory(settings.workspace),
            required=REQUIRED_DOCUMENTS,
        ),
        model=settings.model,
    )


def build_candidate_generator(
    settings: AgentSettings,
    home: Path,
    store: CandidateCreator,
) -> CandidateGenerator:
    """Compose candidate generation over the durable Agent runtime."""
    return CandidateGenerator(
        store=store,
        context_source=CandidateContextSource(default_context_directory(settings.workspace)),
        connector_factory=TraceCandidateConnector,
        agent=CandidateAgent(
            runs=AgentRunStore(home / "core-agent"),
            models=ProductionCandidateModels(settings),
            settings=settings,
            context=ToolContext(home, TraceConnectorApproval(), ()),
            memory_store=JsonlMemoryStore(home / "core-agent" / "memory.jsonl"),
        ),
    )


def build_candidate_image_runner(
    settings: AgentSettings,
    home: Path,
    store: CandidateImageStore,
) -> CandidateImageRunner:
    """Compose the Web candidate image stage over the native Trace connector."""
    return CandidateImageRunner(
        store=store,
        runner=ProductionCandidateTraceRunner(home, settings),
        device_resolver=SimctlDeviceResolver(),
        home=home,
        clock=lambda: datetime.now(UTC),
    )


@dataclass(frozen=True, slots=True)
class ProductionCandidateTraceRunner:
    home: Path
    settings: AgentSettings

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            return build_trace_v1_runner(self.home, http).run(bundle)
