"""Producing candidate drafts, with nowhere to store them.

This is the "script assembly" generator: no tool loop and no web search. Every context
document is read into one instruction, the model answers under a strict output schema, and
one failed validation is retried once before that candidate is given up on.

Nothing here writes a row. The Mac worker runs this for the hosted control plane and hands
the drafts back over HTTP, and the rows are written in D1 by the Worker that published the
job. Keeping production separate from storage is what lets the same instruction, the same
reference sample and the same form assignment serve whichever surface asks for candidates.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ads_booster.candidate_generation.context_source import reference_id
from ads_booster.candidate_generation.errors import (
    CandidateFormatError,
    CandidateGenerationError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.instruction import (
    DEFAULT_COUNTRY,
    DEFAULT_LANGUAGE,
    CaptionForm,
    assign_caption_forms,
    build_instruction,
    build_retry_instruction,
)
from ads_booster.candidate_generation.parsing import parse_candidate_drafts
from ads_booster.workspace import (
    CandidateContextDocument,
    CandidateGenerationProvenance,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ads_booster.candidate_generation.context_source import ReferencePool
    from ads_booster.candidate_generation.models import (
        CandidateContextBundle,
        CandidateDocument,
        CandidateDraft,
    )
    from ads_booster.transport.json_types import JsonValue
    from ads_booster.workspace import CandidateAccountBrief

_PROVIDER_FAILURES = (OSError, RuntimeError)


def default_domain_shuffle(
    domains: Sequence[CandidatePersonaDomain],
) -> tuple[CandidatePersonaDomain, ...]:
    """Break the domain order at random so two batches do not always start the same way."""
    shuffled = list(domains)
    random.shuffle(shuffled)
    return tuple(shuffled)


def assign_domains(
    count: int,
    shuffle: Callable[
        [Sequence[CandidatePersonaDomain]], Sequence[CandidatePersonaDomain]
    ] = default_domain_shuffle,
) -> tuple[CandidatePersonaDomain, ...]:
    """Bind each candidate in an account-less batch to its own domain.

    Coverage counts used to decide this, and the store that kept them is gone. What has to
    survive the store is the 1:1 binding itself: a batch left to pick its own genres writes
    the same one `count` times and reports variety. Drawing without replacement from the
    shuffled vocabulary keeps that guarantee with nothing to remember between batches.
    """
    vocabulary = shuffle(tuple(CandidatePersonaDomain))
    if not vocabulary:
        return ()
    return tuple(vocabulary[index % len(vocabulary)] for index in range(count))


class CandidateDraftClient(Protocol):
    """One provider call: an instruction in, the structured candidates envelope out.

    `call_id` is unique per call within a batch, including the retry of a call. The process
    behind this seam refuses to run twice in the same place, so the id is what lets the
    implementation give each turn its own workspace rather than colliding on the first one.
    """

    def draft(self, instruction: str, *, call_id: str) -> JsonValue: ...


@dataclass(slots=True)
class WrittenTopics:
    """The topics a call must not repeat: the stored history plus this batch so far.

    The batch runs its calls in order, so every call after the first is shown what the ones
    before it actually wrote. That is the only one of the three separation guards that can
    react to the batch as it happens — the domain and the caption form are both decided
    before any call goes out.
    """

    stored: tuple[CandidateHistoryEntry, ...] = ()
    fresh: list[CandidateHistoryEntry] = field(default_factory=list)

    def entries(self) -> tuple[CandidateHistoryEntry, ...]:
        return (*self.fresh, *self.stored)

    def add(self, persona_domain: CandidatePersonaDomain | None, topic: str) -> None:
        self.fresh.insert(0, CandidateHistoryEntry(persona_domain=persona_domain, topic=topic))


@dataclass(frozen=True, slots=True)
class DraftedCandidate:
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

    drafts: tuple[DraftedCandidate, ...]
    failures: int = 0


@dataclass(frozen=True, slots=True)
class _CandidateCall:
    """Everything one candidate's provider call needs, decided before the call goes out."""

    index: int
    bundle: CandidateContextBundle
    pool: ReferencePool
    domain: CandidatePersonaDomain
    form: CaptionForm
    brief: CandidateAccountBrief | None
    country: str
    language: str
    written: WrittenTopics


@dataclass(frozen=True, slots=True)
class CandidateDraftEngine:
    """Assembles the context documents into one provider call per candidate.

    `model` is the model id the run requests; it is recorded on every draft so a reviewer can
    see what produced the caption in front of them.
    """

    client: CandidateDraftClient
    model: str
    # Injected in tests so a sample is predictable; production draws at random.
    sample_references: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]] = (
        random.sample
    )

    def draft(  # noqa: PLR0913 - each argument is one independent input to the batch.
        self,
        *,
        bundle: CandidateContextBundle,
        pool: ReferencePool,
        country: str = DEFAULT_COUNTRY,
        language: str = DEFAULT_LANGUAGE,
        domains: tuple[CandidatePersonaDomain, ...],
        brief: CandidateAccountBrief | None = None,
        history: tuple[CandidateHistoryEntry, ...] = (),
    ) -> CandidateDraftBatch:
        """Write one batch as one provider call per candidate, in order.

        A single call for the whole batch had to keep every candidate distinct by itself,
        and it read the same context every time. One call per candidate lets each draw its
        own reference sample, so the batch collectively sees more of the corpus than any
        one instruction could carry.

        What keeps the candidates apart is assigned before the calls go out: a domain each
        and a caption form each. The recent-topic list is the third guard, and running the
        calls in order rather than in parallel is what makes it real — every call is shown
        the topics the batch has already written, not whichever ones happened to land first.

        `bundle` and `pool` are handed in already loaded rather than read here, so a caller
        can find out that its corpus is unusable before it spends anything on a provider —
        a missing document is an ordinary failed task, and one discovered mid-batch is not.
        """
        forms = assign_caption_forms(len(domains))
        written = WrittenTopics(history)
        drafts: list[DraftedCandidate] = []
        failures: list[CandidateGenerationError] = []
        for index, domain in enumerate(domains):
            call = _CandidateCall(
                index=index,
                bundle=bundle,
                pool=pool,
                domain=domain,
                form=forms[index],
                brief=brief,
                country=country,
                language=language,
                written=written,
            )
            try:
                drafts.append(self._one(call))
            except CandidateGenerationError as error:
                failures.append(error)
        if not drafts:
            raise failures[0]
        return CandidateDraftBatch(drafts=tuple(drafts), failures=len(failures))

    def _one(self, call: _CandidateCall) -> DraftedCandidate:
        """Write one candidate: sample references, ask once, record what it read."""
        sample = call.pool.sample(self.sample_references)
        bundle = call.bundle.model_copy(update={"documents": (*call.bundle.documents, *sample)})
        instruction = build_instruction(
            bundle,
            count=1,
            country=call.country,
            language=call.language,
            domains=(call.domain,),
            history=call.written.entries(),
            account=call.brief,
            forms=(call.form,),
        )
        provenance = self._provenance(bundle, instruction, (call.domain,), sample)
        draft = self._draft(call, instruction)
        # Recorded from the draft rather than from a stored row, so a caller that never
        # stores anything still keeps the rest of the batch off this topic.
        call.written.add(draft.persona_domain, draft.topic)
        return DraftedCandidate(draft=draft, provenance=provenance, caption_form=call.form)

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

    def _draft(self, call: _CandidateCall, instruction: str) -> CandidateDraft:
        call_id = f"{call.index:02d}"
        answer = self._respond(instruction, call_id)
        try:
            return self._parse(answer, call)
        except CandidateFormatError as first_failure:
            retry = f"{instruction}\n\n{build_retry_instruction(first_failure.detail)}"
            return self._parse(self._respond(retry, f"{call_id}-retry"), call)

    def _respond(self, instruction: str, call_id: str) -> JsonValue:
        try:
            return self.client.draft(instruction, call_id=call_id)
        except _PROVIDER_FAILURES as error:
            # The provider's own text is not repeated: it can carry local paths, and the
            # caller chains this exception anyway, so nothing is lost by staying quiet.
            raise CandidateProviderError from error

    @staticmethod
    def _parse(answer: JsonValue, call: _CandidateCall) -> CandidateDraft:
        drafts = parse_candidate_drafts(
            answer,
            expected=1,
            country=call.country,
            domains=(call.domain,),
        )
        return drafts[0]
