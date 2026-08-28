"""Producing candidate drafts, with nowhere to store them.

`ScriptCandidateGenerator` used to do two jobs in one method: assemble the context into a
provider call, and write the row the call produced. That was fine while the only caller had
a local SQLite workspace to write into. The Mac worker does not: it runs the same generation
for the hosted control plane and hands the drafts back over HTTP, and the rows are written in
D1 by the Worker that published the job.

So the half that reads context, sizes a reference sample, assigns a caption form and asks the
model lives here, and the half that stores what came back stays in `script_generator`. The
instruction, the sampling and the form assignment are untouched by the split — that is the
point: the hosted surface has to produce exactly what the local one produces, and the only
way to be sure of that is for both to run this code.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation.context_source import ReferencePool, reference_id
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
from ads_booster.candidate_generation.parsing import parse_candidate_drafts
from ads_booster.providers.errors import ProviderError
from ads_booster.workspace import (
    CandidateContextDocument,
    CandidateGenerationProvenance,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.context_source import (
        CandidateContextSource,
        CandidateReferenceSource,
    )
    from ads_booster.candidate_generation.models import (
        CandidateContextBundle,
        CandidateDocument,
        CandidateDraft,
    )
    from ads_booster.candidate_generation.ports import CandidateModelSource
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import CandidateAccountBrief


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
class GeneratedCandidate:
    """One draft and the record of what the call that produced it actually read."""

    draft: CandidateDraft
    provenance: CandidateGenerationProvenance
    # Which of the batch's caption forms this call was told to write in. Stored beside the
    # provenance rather than inside it because the form is an instruction to this call, not
    # an observation about the corpus, and the hosted surface shows it as its own line.
    caption_form: CaptionForm


@dataclass(frozen=True, slots=True)
class CandidateDraftBatch:
    """What one batch of provider calls produced, including the calls that produced nothing.

    Partial success is the normal outcome — three captions and one timeout is three captions
    worth keeping — so `failures` travels with the drafts rather than being logged away.
    """

    drafts: tuple[GeneratedCandidate, ...]
    failures: int = 0


@dataclass(frozen=True, slots=True)
class _CandidateCall:
    """Everything one candidate's provider call needs, decided before the call goes out."""

    bundle: CandidateContextBundle
    pool: ReferencePool
    domain: CandidatePersonaDomain
    form: CaptionForm
    brief: CandidateAccountBrief | None
    country: str
    written: WrittenTopics


@dataclass(frozen=True, slots=True)
class CandidateDraftEngine:
    """Assembles the context documents into one provider call per candidate.

    `model` is the model id the run requests; it is recorded on every draft so a reviewer can
    see what produced the caption in front of them. Nothing here writes a row: a caller that
    wants candidates stored passes the drafts on to a store, and a caller that wants them sent
    somewhere else sends them.
    """

    models: CandidateModelSource
    context_source: CandidateContextSource
    references: CandidateReferenceSource
    model: str
    # Injected in tests so a sample is predictable; production draws at random.
    sample_references: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]] = (
        random.sample
    )

    def draft(
        self,
        *,
        corpus_country: str,
        draft_country: str,
        domains: tuple[CandidatePersonaDomain, ...],
        brief: CandidateAccountBrief | None = None,
        history: tuple[CandidateHistoryEntry, ...] = (),
    ) -> CandidateDraftBatch:
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

        The two countries are deliberately separate. `corpus_country` picks the reference
        pool, because a batch is grounded in the posts its own country's readers responded
        to. `draft_country` is what the drafts are held to, and it follows the instruction
        template rather than the account: the template names KR, so a batch written for a
        non-KR account still has to come back as KR until the template is parameterised.
        """
        bundle = self.context_source.load()
        pool = self.references.load(corpus_country)
        forms = assign_caption_forms(len(domains))
        written = WrittenTopics(history)
        with ThreadPoolExecutor(max_workers=max(len(domains), 1)) as workers:
            futures = [
                workers.submit(
                    self._one,
                    _CandidateCall(
                        bundle=bundle,
                        pool=pool,
                        domain=domain,
                        form=forms[index],
                        brief=brief,
                        country=draft_country,
                        written=written,
                    ),
                )
                for index, domain in enumerate(domains)
            ]
            drafts: list[GeneratedCandidate] = []
            failures: list[CandidateGenerationError] = []
            for future in futures:
                try:
                    drafts.append(future.result())
                except CandidateGenerationError as error:
                    failures.append(error)
        if not drafts:
            raise failures[0]
        return CandidateDraftBatch(drafts=tuple(drafts), failures=len(failures))

    def _one(self, call: _CandidateCall) -> GeneratedCandidate:
        """Write one candidate: sample references, ask once, record what it read."""
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
            drafts = self._drafts(client, instruction, (call.domain,), call.country)
        draft = drafts[0]
        # Recorded from the draft rather than from a stored row, so a caller that never
        # stores anything still keeps the rest of the batch off this topic.
        call.written.add(draft.persona_domain, draft.topic)
        return GeneratedCandidate(draft=draft, provenance=provenance, caption_form=call.form)

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
        country: str,
    ) -> tuple[CandidateDraft, ...]:
        history: tuple[JsonObject, ...] = ({"role": "user", "content": instruction},)
        answer = self._respond(client, history)
        try:
            return self._parse(answer, domains, country)
        except CandidateFormatError as first_failure:
            retry_history: tuple[JsonObject, ...] = (
                *history,
                {"role": "assistant", "content": answer},
                {"role": "user", "content": build_retry_instruction(first_failure.detail)},
            )
            return self._parse(self._respond(client, retry_history), domains, country)

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
        self,
        answer: str,
        domains: tuple[CandidatePersonaDomain, ...],
        country: str,
    ) -> tuple[CandidateDraft, ...]:
        return parse_candidate_drafts(
            answer,
            expected=len(domains),
            country=country,
            domains=domains,
        )
