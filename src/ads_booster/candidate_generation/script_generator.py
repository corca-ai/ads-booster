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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation.context_source import (
    CandidateReferenceSource,
    ReferencePool,
    reference_id,
)
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateFormatError,
    CandidateGenerationError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.instruction import (
    CaptionForm,
    assign_caption_forms,
    build_instruction,
    build_retry_instruction,
)
from ads_booster.candidate_generation.models import CandidateBatch, CandidateDocument
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


@dataclass(slots=True)
class WrittenTopics:
    """The topics a call must not repeat: the stored history plus this batch so far.

    Shared across the batch's threads, so every read and write is under the lock. A call
    sees whatever had been written when its instruction was built, which is why the domain
    and form assignments — decided before any call goes out — carry the real separation.
    """

    stored: tuple[CandidateHistoryEntry, ...]
    fresh: list[CandidateHistoryEntry] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def entries(self) -> tuple[CandidateHistoryEntry, ...]:
        with self.lock:
            return (*self.fresh, *self.stored)

    def add(self, persona_domain: CandidatePersonaDomain | None, topic: str) -> None:
        with self.lock:
            self.fresh.insert(0, CandidateHistoryEntry(persona_domain=persona_domain, topic=topic))


@dataclass(frozen=True, slots=True)
class _CandidateCall:
    """Everything one candidate's provider call needs, decided before the call goes out."""

    bundle: CandidateContextBundle
    pool: ReferencePool
    domain: CandidatePersonaDomain
    form: CaptionForm
    brief: CandidateAccountBrief | None
    account_id: MarketingAccountId | None
    written: WrittenTopics


@dataclass(frozen=True, slots=True)
class ScriptCandidateGenerator:
    """Assembles the context documents into one provider call and stores its candidates.

    `model` is the model id the run requests; it is recorded on every candidate the batch
    writes so a reviewer can see what produced the caption in front of them.
    """

    store: CandidateWriter
    models: CandidateModelSource
    context_source: CandidateContextSource
    references: CandidateReferenceSource
    model: str
    count: int = DEFAULT_CANDIDATE_COUNT
    country: str = DEFAULT_COUNTRY
    history_limit: int = DEFAULT_HISTORY_LIMIT
    # Injected in tests so a sample is predictable; production draws at random.
    sample_references: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]] = (
        random.sample
    )
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
    ) -> CandidateBatch:
        """Write one batch as one provider call per candidate, run in parallel.

        A single call for the whole batch had to keep every candidate distinct by itself,
        and it read the same context every time. One call per candidate lets each draw its
        own reference sample, so the batch collectively sees more of the corpus than any
        one instruction could carry.

        What keeps the candidates apart is assigned before the calls go out: a domain each
        and a caption form each. The recent-topic list is the third guard and the only one
        that cannot be fully resolved up front — a call reads the topics stored when its
        instruction is built, so calls that start later in the pool see more of the batch.
        Under full parallelism that is best effort, and the two assignments are what
        actually carry the separation.

        With an account the batch is that one person writing about different things, so
        coverage stops choosing domains and the account's own domain applies to all of
        them. The history is scoped to that account for the same reason: what one account
        has already written is a fact about that account.
        """
        del run_context
        bundle = self.context_source.load()
        # The corpus follows the account, not a constant: a batch is grounded in the posts
        # its own country's readers responded to. Without an account the workspace-wide
        # default stands in.
        country = self.country if account is None else account.country
        pool = self.references.load(country)
        brief = None if account is None else CandidateAccountBrief.of(account)
        account_id = None if account is None else account.account_id
        domains = (
            (brief.domain,) * self.count
            if brief is not None
            else assign_domains(
                self.store.count_candidate_domains(workspace_id), self.count, self.shuffle
            )
        )
        # The cap of one testimonial belongs to the batch, so the batch assigns the forms
        # and hands each call its own.
        forms = assign_caption_forms(self.count)
        written = WrittenTopics(
            self.store.recent_candidate_history(
                workspace_id, self.history_limit, account_id=account_id
            )
        )
        with ThreadPoolExecutor(max_workers=self.count) as workers:
            futures = [
                workers.submit(
                    self._one,
                    workspace_id,
                    _CandidateCall(
                        bundle=bundle,
                        pool=pool,
                        domain=domains[index],
                        form=forms[index],
                        brief=brief,
                        account_id=account_id,
                        written=written,
                    ),
                )
                for index in range(self.count)
            ]
            records: list[CandidateRecord] = []
            failures: list[CandidateGenerationError] = []
            for future in futures:
                try:
                    records.append(future.result())
                except CandidateGenerationError as error:
                    failures.append(error)
        if not records:
            raise failures[0]
        return CandidateBatch(records=tuple(records), failures=len(failures))

    def _one(self, workspace_id: WorkspaceId, call: _CandidateCall) -> CandidateRecord:
        """Write one candidate: sample references, ask once, store the row."""
        sample = call.pool.sample(self.sample_references)
        bundle = call.bundle.model_copy(update={"documents": (*call.bundle.documents, *sample)})
        instruction = build_instruction(
            bundle,
            count=1,
            domains=(call.domain,),
            history=call.written.entries(),
            account=call.brief,
            forms=(call.form,),
        )
        provenance = self._provenance(bundle, instruction, (call.domain,), sample)
        with self.models.open() as client:
            drafts = self._drafts(client, instruction, (call.domain,))
        record = self.store.create_candidate(
            self._create(workspace_id, drafts[0], provenance, call.account_id)
        )
        call.written.add(record.persona_domain, record.topic)
        return record

    def _provenance(
        self,
        bundle: CandidateContextBundle,
        instruction: str,
        domains: tuple[CandidatePersonaDomain, ...],
        sample: tuple[CandidateDocument, ...] = (),
    ) -> CandidateGenerationProvenance:
        """Record what this run read, just before it spends the provider call on it."""
        return CandidateGenerationProvenance(
            reference_ids=tuple(reference_id(document) for document in sample),
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
            answer,
            expected=len(domains) or self.count,
            country=self.country,
            domains=domains or None,
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
