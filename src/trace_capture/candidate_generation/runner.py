from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from trace_capture.auth.codex import OAuthError
from trace_capture.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateFormatError,
    CandidateProviderError,
)
from trace_capture.candidate_generation.instruction import (
    build_instruction,
    build_retry_instruction,
)
from trace_capture.candidate_generation.parsing import parse_candidate_drafts
from trace_capture.providers.errors import ProviderError
from trace_capture.workspace import CandidateCreate, CandidateSource

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from trace_capture.agent.session import ModelClient
    from trace_capture.candidate_generation.context_source import CandidateContextSource
    from trace_capture.candidate_generation.models import CandidateDraft
    from trace_capture.transport.json_types import JsonObject
    from trace_capture.workspace import CandidateRecord, WorkspaceId

DEFAULT_CANDIDATE_COUNT: Final = 3
DEFAULT_COUNTRY: Final = "KR"


class CandidateWriter(Protocol):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord: ...


class CandidateModelSource(Protocol):
    def open(self) -> AbstractContextManager[ModelClient]: ...


class CandidateGeneratorPort(Protocol):
    def generate(self, workspace_id: WorkspaceId) -> tuple[CandidateRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class CandidateGenerator:
    """Assembles the context documents into one provider call and stores its candidates.

    This is the "script assembly" mode: no tool loop and no web search. The model is
    called once, its strict JSON is validated, and one failed validation is retried once
    before the run gives up without writing anything.
    """

    store: CandidateWriter
    models: CandidateModelSource
    context_source: CandidateContextSource
    count: int = DEFAULT_CANDIDATE_COUNT
    country: str = DEFAULT_COUNTRY

    def generate(self, workspace_id: WorkspaceId) -> tuple[CandidateRecord, ...]:
        bundle = self.context_source.load()
        instruction = build_instruction(bundle, count=self.count)
        with self.models.open() as client:
            drafts = self._drafts(client, instruction)
        return tuple(
            self.store.create_candidate(self._create(workspace_id, draft)) for draft in drafts
        )

    def _drafts(self, client: ModelClient, instruction: str) -> tuple[CandidateDraft, ...]:
        history: tuple[JsonObject, ...] = ({"role": "user", "content": instruction},)
        answer = self._respond(client, history)
        try:
            return self._parse(answer)
        except CandidateFormatError as first_failure:
            retry_history: tuple[JsonObject, ...] = (
                *history,
                {"role": "assistant", "content": answer},
                {"role": "user", "content": build_retry_instruction(first_failure.detail)},
            )
            return self._parse(self._respond(client, retry_history))

    def _respond(self, client: ModelClient, history: tuple[JsonObject, ...]) -> str:
        try:
            return client.respond(history, ()).text
        except OAuthError as error:
            raise CandidateAuthRequiredError from error
        except ProviderError as error:
            raise CandidateProviderError(context_overflow=error.context_overflow) from error

    def _parse(self, answer: str) -> tuple[CandidateDraft, ...]:
        return parse_candidate_drafts(answer, expected=self.count, country=self.country)

    def _create(self, workspace_id: WorkspaceId, draft: CandidateDraft) -> CandidateCreate:
        return CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.AUTO,
            country=draft.country,
            topic=draft.topic,
            caption=draft.caption,
            hypothesis=draft.hypothesis,
            refs_used=draft.refs_used,
            principles_applied=draft.principles_applied,
            shooting_order=draft.appium_prompt,
        )
