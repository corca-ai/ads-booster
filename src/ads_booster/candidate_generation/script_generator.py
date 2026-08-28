"""Single-call candidate generation from the assembled Korean context documents.

This is the "script assembly" generator: no tool loop and no web search. Every context
document is read into one instruction, the model answers with a strict JSON array, and one
failed validation is retried once before the run gives up without writing anything. The
Agent-kernel connector in `agent_generator` remains available for the tool-loop path; this
module is what the Web generate route runs.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateFormatError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.instruction import (
    build_instruction,
    build_retry_instruction,
)
from ads_booster.candidate_generation.parsing import parse_candidate_drafts
from ads_booster.providers.errors import ProviderError
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidateContextDocument,
    CandidateCreate,
    CandidateGenerationProvenance,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
    CandidateSource,
    MarketingAccountId,
    MarketingAccountRecord,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.context_source import CandidateContextSource
    from ads_booster.candidate_generation.models import CandidateContextBundle, CandidateDraft
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import CandidateRecord, WorkspaceId

DEFAULT_CANDIDATE_COUNT: Final = 3
DEFAULT_COUNTRY: Final = "KR"
DEFAULT_HISTORY_LIMIT: Final = 15


def default_domain_shuffle(
    domains: Sequence[CandidatePersonaDomain],
) -> tuple[CandidatePersonaDomain, ...]:
    """Break coverage ties at random so a cold workspace does not always start the same way."""
    shuffled = list(domains)
    random.shuffle(shuffled)
    return tuple(shuffled)


def assign_domains(
    counts: Mapping[str, int],
    count: int,
    shuffle: Callable[
        [Sequence[CandidatePersonaDomain]], Sequence[CandidatePersonaDomain]
    ] = default_domain_shuffle,
) -> tuple[CandidatePersonaDomain, ...]:
    """Pick the `count` least-covered domains, ties broken by the injected shuffle.

    Shuffling before the sort rather than after is what makes the tie-break fair: Python's
    sort is stable, so equal counts keep the shuffled order while the counts themselves
    still decide everything else. A domain with no rows is absent from `counts` and
    therefore sorts at zero, which is exactly the "never written yet" case we want first.
    """
    shuffled = shuffle(tuple(CandidatePersonaDomain))
    ranked = sorted(shuffled, key=lambda domain: counts.get(domain.value, 0))
    return tuple(ranked[:count])


class CandidateWriter(Protocol):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord: ...

    def count_candidate_domains(self, workspace_id: WorkspaceId) -> dict[str, int]: ...

    def recent_candidate_history(
        self,
        workspace_id: WorkspaceId,
        limit: int,
        *,
        account_id: MarketingAccountId | None = None,
    ) -> tuple[CandidateHistoryEntry, ...]: ...


class CandidateModelSource(Protocol):
    def open(self) -> AbstractContextManager[ModelClient]: ...


@dataclass(frozen=True, slots=True)
class ScriptCandidateGenerator:
    """Assembles the context documents into one provider call and stores its candidates.

    `model` is the model id the run requests; it is recorded on every candidate the batch
    writes so a reviewer can see what produced the caption in front of them.
    """

    store: CandidateWriter
    models: CandidateModelSource
    context_source: CandidateContextSource
    model: str
    count: int = DEFAULT_CANDIDATE_COUNT
    country: str = DEFAULT_COUNTRY
    history_limit: int = DEFAULT_HISTORY_LIMIT
    # Injected in tests so a coverage tie resolves predictably; production randomises it.
    shuffle: Callable[[Sequence[CandidatePersonaDomain]], Sequence[CandidatePersonaDomain]] = (
        default_domain_shuffle
    )

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
        account: MarketingAccountRecord | None = None,
    ) -> tuple[CandidateRecord, ...]:
        """Write one batch, either for an account or for the workspace at large.

        With an account the batch is that one person writing about different things, so
        coverage stops choosing domains and the account's own domain applies to all of
        them. Without one the previous behaviour stands: spread the batch across the
        domains this workspace has covered least.

        The "do not repeat these" history is scoped the same way. What one account has
        already written is a fact about that account; read workspace-wide it made every
        account steer around every other account's subjects.
        """
        del run_context
        bundle = self.context_source.load()
        brief = None if account is None else CandidateAccountBrief.of(account)
        account_id = None if account is None else account.account_id
        domains = (
            (brief.domain,) * self.count
            if brief is not None
            else assign_domains(
                self.store.count_candidate_domains(workspace_id), self.count, self.shuffle
            )
        )
        history = self.store.recent_candidate_history(
            workspace_id, self.history_limit, account_id=account_id
        )
        instruction = build_instruction(
            bundle, count=self.count, domains=domains, history=history, account=brief
        )
        provenance = self._provenance(bundle, instruction, domains)
        with self.models.open() as client:
            drafts = self._drafts(client, instruction, domains)
        return tuple(
            self.store.create_candidate(self._create(workspace_id, draft, provenance, account_id))
            for draft in drafts
        )

    def _provenance(
        self,
        bundle: CandidateContextBundle,
        instruction: str,
        domains: tuple[CandidatePersonaDomain, ...],
    ) -> CandidateGenerationProvenance:
        """Record what this run read, just before it spends the provider call on it."""
        return CandidateGenerationProvenance(
            documents=tuple(
                CandidateContextDocument(
                    relative_path=document.relative_path,
                    size_bytes=len(document.text.encode("utf-8")),
                )
                for document in bundle.documents
            ),
            model=self.model,
            instruction_chars=len(instruction),
            generated_at=time.time(),
            assigned_domains=domains,
        )

    def _drafts(
        self,
        client: ModelClient,
        instruction: str,
        domains: tuple[CandidatePersonaDomain, ...],
    ) -> tuple[CandidateDraft, ...]:
        history: tuple[JsonObject, ...] = ({"role": "user", "content": instruction},)
        answer = self._respond(client, history)
        try:
            return self._parse(answer, domains)
        except CandidateFormatError as first_failure:
            retry_history: tuple[JsonObject, ...] = (
                *history,
                {"role": "assistant", "content": answer},
                {"role": "user", "content": build_retry_instruction(first_failure.detail)},
            )
            return self._parse(self._respond(client, retry_history), domains)

    def _respond(self, client: ModelClient, history: tuple[JsonObject, ...]) -> str:
        try:
            return client.respond(history, ()).text
        except OAuthError as error:
            raise CandidateAuthRequiredError from error
        except ProviderError as error:
            raise CandidateProviderError(
                context_overflow=error.context_overflow,
                provider_code=error.code,
            ) from error

    def _parse(
        self, answer: str, domains: tuple[CandidatePersonaDomain, ...]
    ) -> tuple[CandidateDraft, ...]:
        return parse_candidate_drafts(
            answer, expected=self.count, country=self.country, domains=domains or None
        )

    def _create(
        self,
        workspace_id: WorkspaceId,
        draft: CandidateDraft,
        provenance: CandidateGenerationProvenance,
        account_id: MarketingAccountId | None = None,
    ) -> CandidateCreate:
        return CandidateCreate(
            account_id=account_id,
            workspace_id=workspace_id,
            source=CandidateSource.AUTO,
            country=draft.country,
            posting_slot=draft.posting_slot,
            topic=draft.topic,
            persona_domain=draft.persona_domain,
            caption=draft.caption,
            hypothesis=draft.hypothesis,
            image_inputs=draft.image_inputs,
            refs_used=draft.refs_used,
            principles_applied=draft.principles_applied,
            shooting_order=draft.appium_prompt,
            generation_provenance=provenance,
        )
