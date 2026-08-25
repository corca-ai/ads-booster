from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trace_capture.auth.codex import CodexOAuth
from trace_capture.auth.store import AuthStore
from trace_capture.candidate_generation.context_source import (
    CandidateContextSource,
    default_context_directory,
)
from trace_capture.candidate_generation.instruction import SYSTEM_INSTRUCTION
from trace_capture.candidate_generation.runner import CandidateGenerator, CandidateWriter
from trace_capture.providers.codex import CodexResponsesClient
from trace_capture.transport.http import create_http_client

if TYPE_CHECKING:
    from collections.abc import Generator

    from trace_capture.agent.session import ModelClient
    from trace_capture.config.settings import AgentSettings


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
    )
