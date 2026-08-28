from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ads_booster.auth.codex import CodexOAuth
from ads_booster.auth.store import AuthStore
from ads_booster.candidate_generation.account_proposal import AccountProposalGenerator
from ads_booster.candidate_generation.background_factory import ProductionCandidateBackgrounds
from ads_booster.candidate_generation.context_source import (
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    CandidateReferenceSource,
    default_context_directory,
)
from ads_booster.candidate_generation.draft_engine import CandidateDraftEngine
from ads_booster.candidate_generation.instruction import SYSTEM_INSTRUCTION
from ads_booster.candidate_generation.kernel import (
    CandidateGenerator,
    CandidateImageRunner,
    build_judged_codex_trace_runner,
    build_kernel_candidate_generator,
)
from ads_booster.candidate_generation.local_image_runner import (
    CandidateImageOptions,
    LocalCandidateImageRunner,
)
from ads_booster.candidate_generation.script_generator import (
    CandidateWriter,
    ScriptCandidateGenerator,
)
from ads_booster.default_assets import (
    default_iphone_ui_path,
    default_trace_components_path,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.native_capture import SimctlDeviceResolver
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.transport.http import create_http_client

COMPONENT_FIXTURE_ENVIRONMENT: Final = "TRACE_AGENT_TRACE_COMPONENTS"
IPHONE_UI_ENVIRONMENT: Final = "TRACE_AGENT_IPHONE_UI"

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.ports import CandidateCreator, CandidateImageStore
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


def build_account_proposal_generator(settings: AgentSettings) -> AccountProposalGenerator:
    """Compose the account proposal generator from settings and the context directory.

    It reads the same context directory the caption engine does, because the evidence it
    argues from — the reference index — is the same corpus.
    """
    return AccountProposalGenerator(
        models=ProductionCandidateModels(settings, instructions=SYSTEM_INSTRUCTION),
        context_directory=default_context_directory(settings.workspace),
        model=settings.model,
    )


def build_script_candidate_generator(
    settings: AgentSettings,
    store: CandidateWriter,
) -> ScriptCandidateGenerator:
    """Compose the single-call generation engine from settings and the context directory."""
    directory = default_context_directory(settings.workspace)
    return ScriptCandidateGenerator(
        store=store,
        models=ProductionCandidateModels(settings, instructions=SYSTEM_INSTRUCTION),
        context_source=CandidateContextSource(directory, required=REQUIRED_DOCUMENTS),
        references=CandidateReferenceSource(directory),
        model=settings.model,
    )


def build_worker_draft_engine(settings: AgentSettings) -> CandidateDraftEngine:
    """Compose the draft half of the engine for a host with no workspace to write into.

    This is what the Mac worker runs for the hosted control plane: the same context
    directory, the same instruction and the same model as
    `build_script_candidate_generator`, minus the store — the rows for a hosted batch are
    written in D1 by the Worker that published the job.
    """
    directory = default_context_directory(settings.workspace)
    return CandidateDraftEngine(
        models=ProductionCandidateModels(settings, instructions=SYSTEM_INSTRUCTION),
        context_source=CandidateContextSource(directory, required=REQUIRED_DOCUMENTS),
        references=CandidateReferenceSource(directory),
        model=settings.model,
    )


def build_candidate_generator(
    settings: AgentSettings,
    home: Path,
    store: CandidateCreator,
) -> CandidateGenerator:
    """Compose candidate generation over the durable Agent runtime.

    The wiring itself lives in `kernel/candidate_batch.py`; this only supplies the provider
    source, so the shared composition root never names a run store or a connector.
    """
    return build_kernel_candidate_generator(
        settings,
        home,
        store,
        ProductionCandidateModels(settings),
    )


def build_candidate_image_runner(
    settings: AgentSettings,
    home: Path,
    store: CandidateImageStore,
) -> CandidateImageRunner:
    """Compose the Web candidate image stage over the native Trace connector.

    The local composition is composed alongside it and used whenever this host cannot run
    the native capture — no Simulator resolves, or no Codex CLI is installed to drive one.
    Both write the same judged background; they differ in what draws the Trace layer on
    top of it, and the candidate records which one ran.
    """
    return CandidateImageRunner(
        store=store,
        runner=ProductionCandidateTraceRunner(home, settings),
        device_resolver=SimctlDeviceResolver(),
        home=home,
        clock=lambda: datetime.now(UTC),
        fallback=build_local_candidate_image_runner(settings, home, store),
    )


def build_local_candidate_image_runner(
    settings: AgentSettings,
    home: Path,
    store: CandidateImageStore,
) -> LocalCandidateImageRunner:
    """Compose the local composition from settings, packaged assets, and the state root."""
    return LocalCandidateImageRunner(
        store=store,
        backgrounds=ProductionCandidateBackgrounds(settings),
        options=CandidateImageOptions(
            home=home,
            component_fixture=resolve_asset(
                settings.workspace,
                COMPONENT_FIXTURE_ENVIRONMENT,
                default_trace_components_path(),
            ),
            iphone_ui_path=resolve_asset(
                settings.workspace,
                IPHONE_UI_ENVIRONMENT,
                default_iphone_ui_path(),
            ),
        ),
    )


def resolve_asset(workspace: Path, environment: str, packaged: Path) -> Path:
    """Resolve a packaged image asset, honouring an absolute or cwd-relative override.

    The assets ship inside the installed package, so the default never depends on the
    directory the service was started from; only an explicit override is resolved against it.
    """
    configured = os.environ.get(environment)
    if configured is None:
        return packaged
    path = Path(configured).expanduser()
    return path if path.is_absolute() else workspace / path


_NATIVE_RUNNER_UNAVAILABLE: Final = "native_runner_unavailable"


@dataclass(frozen=True, slots=True)
class ProductionCandidateTraceRunner:
    home: Path
    settings: AgentSettings

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            try:
                runner = build_judged_codex_trace_runner(self.home, http, self.settings)
            except CodexCliError as error:
                # A host without the Codex CLI cannot run the native capture at all. That is
                # the same kind of fact as having no device, so it is reported in the same
                # vocabulary and the image stage can fall back instead of returning a 500.
                raise MarketingExecutionError(_NATIVE_RUNNER_UNAVAILABLE) from error
            return runner.run(bundle)
