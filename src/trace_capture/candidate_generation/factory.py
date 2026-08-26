from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from trace_capture.auth.codex import CodexOAuth
from trace_capture.auth.store import AuthStore
from trace_capture.candidate_generation.context_source import (
    CandidateContextSource,
    default_context_directory,
)
from trace_capture.candidate_generation.image_runner import (
    CandidateImageOptions,
    CandidateImageRunner,
    CandidateImageStore,
)
from trace_capture.candidate_generation.instruction import SYSTEM_INSTRUCTION
from trace_capture.candidate_generation.runner import CandidateGenerator, CandidateWriter
from trace_capture.default_assets import (
    default_iphone_ui_path,
    default_trace_components_path,
)
from trace_capture.providers.codex import CodexResponsesClient
from trace_capture.search.image.background import ImageSearchBackgroundFetcher
from trace_capture.search.image.providers import create_image_search_provider
from trace_capture.transport.http import create_http_client

if TYPE_CHECKING:
    from collections.abc import Generator

    from trace_capture.agent.session import ModelClient
    from trace_capture.candidate_generation.image_runner import CandidateBackgroundPort
    from trace_capture.config.settings import AgentSettings

COMPONENT_FIXTURE_ENVIRONMENT: Final = "TRACE_AGENT_TRACE_COMPONENTS"
IPHONE_UI_ENVIRONMENT: Final = "TRACE_AGENT_IPHONE_UI"
SEARCH_PROVIDER_ENVIRONMENT: Final = "TRACE_AGENT_WEB_SEARCH_PROVIDER"
SEARCH_TIMEOUT_ENVIRONMENT: Final = "TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS"


@dataclass(frozen=True, slots=True)
class ProductionCandidateModels:
    """Opens one provider client per generation run, using the host OAuth credential."""

    settings: AgentSettings

    @contextmanager
    def open(self) -> Generator[ModelClient]:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            yield CodexResponsesClient(
                http=http,
                oauth=CodexOAuth(http=http, store=AuthStore.default()),
                model=self.settings.model,
                instructions=SYSTEM_INSTRUCTION,
            )


def build_candidate_generator(
    settings: AgentSettings,
    store: CandidateWriter,
) -> CandidateGenerator:
    """Compose the production generator from settings, the OAuth store, and the context dir."""
    return CandidateGenerator(
        store=store,
        models=ProductionCandidateModels(settings),
        context_source=CandidateContextSource(default_context_directory(settings.workspace)),
        model=settings.model,
    )


@dataclass(frozen=True, slots=True)
class ProductionCandidateBackgrounds:
    """Opens one image-search background fetcher per image run.

    The fetcher keeps the provider allowlist and provenance checks of the shared
    `ImageSearchBackgroundFetcher`; this factory only supplies its transport.
    """

    settings: AgentSettings

    @contextmanager
    def open(self) -> Generator[CandidateBackgroundPort]:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            yield ImageSearchBackgroundFetcher(
                image_search=create_image_search_provider(
                    http=http,
                    provider_name=os.environ.get(SEARCH_PROVIDER_ENVIRONMENT, "auto"),
                    timeout_seconds=float(os.environ.get(SEARCH_TIMEOUT_ENVIRONMENT, "30")),
                ),
                http=http,
            )


def resolve_asset(workspace: Path, environment: str, packaged: Path) -> Path:
    """Resolve a packaged image asset, honouring an absolute or cwd-relative override.

    The assets ship inside the installed package, so the default never depends on the
    directory the service was started from; only an explicit override is resolved against it.
    """
    configured = os.environ.get(environment)
    return packaged if configured is None else _absolute_or_workspace(workspace, configured)


def build_candidate_image_runner(
    settings: AgentSettings,
    home: Path,
    store: CandidateImageStore,
) -> CandidateImageRunner:
    """Compose the offline image runner from settings, packaged assets, and the state root."""
    return CandidateImageRunner(
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


def _absolute_or_workspace(workspace: Path, configured: str) -> Path:
    path = Path(configured).expanduser()
    return path if path.is_absolute() else workspace / path
