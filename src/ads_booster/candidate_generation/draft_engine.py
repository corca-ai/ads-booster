"""Producing candidate drafts, with nowhere to store them.

This is the "script assembly" generator: no tool loop and no web search. The context
documents and a sample of the country's reference bodies go into one instruction, the model
answers under a strict output schema, and one failed validation is retried once.

A batch is one provider call. What makes its candidates different from each other is decided
here before the call goes out — a caption form, a persona domain and a subject axis each —
and stated per candidate in the instruction, so the answer can be held to the assignment
rather than trusted to have varied on its own.

Nothing here writes a row. The Mac worker runs this for the hosted control plane and hands
the drafts back over HTTP, and the rows are written in D1 by the Worker that published the
job. Keeping production separate from storage is what lets the same instruction, the same
reference sample and the same assignment serve whichever surface asks for candidates.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.candidate_generation.context_source import reference_id
from ads_booster.candidate_generation.errors import (
    CandidateFormatError,
    CandidateGenerationError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.instruction import (
    DEFAULT_COUNTRY,
    DEFAULT_LANGUAGE,
    assign_candidates,
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
    from ads_booster.candidate_generation.instruction import CandidateAssignment, CaptionForm
    from ads_booster.candidate_generation.models import (
        CandidateContextBundle,
        CandidateDocument,
        CandidateDraft,
    )
    from ads_booster.transport.json_types import JsonValue
    from ads_booster.workspace import CandidateAccountBrief

_PROVIDER_FAILURES = (OSError, RuntimeError)
# How many candidates one provider call may be asked for. The Codex turn behind this seam
# runs under a wall-clock limit, and four full captions with their schedules and shooting
# orders is already a long answer; a larger batch risks the whole call to save one.
DEFAULT_MAX_BATCH: Final = 4
# Reference bodies drawn per batch: what worked, and enough of what did not to say where the
# line is. One call carries the whole batch now, so it can afford a wider sample than the
# three-and-one that a per-candidate call used to take.
DEFAULT_HIT_SAMPLES: Final = 6
DEFAULT_FLOP_SAMPLES: Final = 2
# The ceiling an instruction is shrunk to fit. The corpus keeps growing and the provider's
# context does not, so the sample gives way before the core documents do.
DEFAULT_MAX_INSTRUCTION_CHARS: Final = 60_000
_MIN_HIT_SAMPLES: Final = 1
_MIN_FLOP_SAMPLES: Final = 1


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

    `call_id` is unique per call within a request, including a retry. The process behind
    this seam refuses to run twice in the same place, so the id is what lets the
    implementation give each turn its own workspace rather than colliding on the first one.
    """

    def draft(self, instruction: str, *, call_id: str) -> JsonValue: ...


@dataclass(slots=True)
class WrittenTopics:
    """The topics a call must not repeat: the history it was handed plus this run so far.

    A request larger than one batch is several calls in order, and each is shown what the
    ones before it wrote. That is the whole reason those calls are sequential.
    """

    stored: tuple[CandidateHistoryEntry, ...] = ()
    fresh: list[CandidateHistoryEntry] = field(default_factory=list)

    def entries(self) -> tuple[CandidateHistoryEntry, ...]:
        return (*self.fresh, *self.stored)

    def add(self, persona_domain: CandidatePersonaDomain | None, topic: str) -> None:
        self.fresh.insert(0, CandidateHistoryEntry(persona_domain=persona_domain, topic=topic))


@dataclass(frozen=True, slots=True)
class DraftedCandidate:
    """One draft, what the call that produced it read, and what it was told to be."""

    draft: CandidateDraft
    # Shared by every candidate from the same call, because it records that call: the
    # documents it carried, the sample it drew, the instruction it spent.
    provenance: CandidateGenerationProvenance
    # This candidate's own share of the assignment. Kept beside the provenance rather than
    # inside it because these are instructions to the model, not observations about the
    # corpus, and the hosted surface shows them per candidate.
    caption_form: CaptionForm
    assigned_interest: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDraftBatch:
    """What one request produced, including the candidates it did not get.

    A request larger than one batch is several calls, so partial success is possible: one
    batch of four and one failed call is four captions worth keeping. `failures` counts the
    candidates that were asked for and did not arrive, and `failure_reason` says why.
    """

    drafts: tuple[DraftedCandidate, ...]
    failures: int = 0
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDraftEngine:
    """Assembles the context documents into one provider call per batch of candidates.

    `model` is the model id the run requests; it is recorded on every draft so a reviewer can
    see what produced the caption in front of them.
    """

    client: CandidateDraftClient
    model: str
    # Injected in tests so a sample is predictable; production draws at random.
    sample_references: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]] = (
        random.sample
    )
    max_batch: int = DEFAULT_MAX_BATCH
    hit_samples: int = DEFAULT_HIT_SAMPLES
    flop_samples: int = DEFAULT_FLOP_SAMPLES
    max_instruction_chars: int = DEFAULT_MAX_INSTRUCTION_CHARS

    def draft(  # noqa: PLR0913 - each argument is one independent input to the request.
        self,
        *,
        bundle: CandidateContextBundle,
        pool: ReferencePool,
        country: str = DEFAULT_COUNTRY,
        language: str = DEFAULT_LANGUAGE,
        domains: tuple[CandidatePersonaDomain, ...],
        brief: CandidateAccountBrief | None = None,
        interests: Sequence[str] = (),
        history: tuple[CandidateHistoryEntry, ...] = (),
    ) -> CandidateDraftBatch:
        """Write the requested candidates, one provider call per batch of `max_batch`.

        A request no larger than one batch is a single call, which is the normal case. A
        larger one is several calls in order, each shown what the earlier ones wrote, so the
        second batch does not restate the first.

        `bundle` and `pool` are handed in already loaded rather than read here, so a caller
        can find out that its corpus is unusable before it spends anything on a provider —
        a missing document is an ordinary failed task, and one discovered mid-request is not.
        """
        assignments = assign_candidates(domains, interests)
        written = WrittenTopics(history)
        drafts: list[DraftedCandidate] = []
        failures = 0
        failure: CandidateGenerationError | None = None
        for index, chunk in enumerate(self._batches(assignments)):
            try:
                produced = self._one(
                    index=index,
                    bundle=bundle,
                    pool=pool,
                    assignments=chunk,
                    brief=brief,
                    country=country,
                    language=language,
                    history=written.entries(),
                )
            except CandidateGenerationError as error:
                failures += len(chunk)
                failure = error
                continue
            for drafted in produced:
                written.add(drafted.draft.persona_domain, drafted.draft.topic)
            # A model that answered short costs the candidates it left out, not the batch.
            failures += len(chunk) - len(produced)
            drafts.extend(produced)
        if not drafts:
            # Raised rather than reported, and as itself: a request that produced nothing is
            # a failed task, and the caller decides what a format failure means differently
            # from what a dead process means.
            raise failure if failure is not None else CandidateProviderError
        return CandidateDraftBatch(
            drafts=tuple(drafts),
            failures=failures,
            failure_reason=None if failure is None else failure.message,
        )

    def _batches(
        self,
        assignments: tuple[CandidateAssignment, ...],
    ) -> tuple[tuple[CandidateAssignment, ...], ...]:
        size = max(1, self.max_batch)
        return tuple(
            assignments[start : start + size] for start in range(0, len(assignments), size)
        )

    def _one(  # noqa: PLR0913 - each argument is one independent input to the call.
        self,
        *,
        index: int,
        bundle: CandidateContextBundle,
        pool: ReferencePool,
        assignments: tuple[CandidateAssignment, ...],
        brief: CandidateAccountBrief | None,
        country: str,
        language: str,
        history: tuple[CandidateHistoryEntry, ...],
    ) -> tuple[DraftedCandidate, ...]:
        """Write one batch: size the sample to fit, ask once, record what the call read."""
        sampled, instruction = self._instruction(
            bundle=bundle,
            pool=pool,
            assignments=assignments,
            brief=brief,
            country=country,
            language=language,
            history=history,
        )
        provenance = self._provenance(bundle, instruction, assignments, sampled)
        drafts = self._draft(f"{index:02d}", instruction, assignments, country)
        return tuple(
            DraftedCandidate(
                draft=draft,
                provenance=provenance,
                caption_form=assignments[position].form,
                assigned_interest=assignments[position].interest,
            )
            for position, draft in enumerate(drafts)
        )

    def _instruction(  # noqa: PLR0913 - each argument is one independent prompt input.
        self,
        *,
        bundle: CandidateContextBundle,
        pool: ReferencePool,
        assignments: tuple[CandidateAssignment, ...],
        brief: CandidateAccountBrief | None,
        country: str,
        language: str,
        history: tuple[CandidateHistoryEntry, ...],
    ) -> tuple[tuple[CandidateDocument, ...], str]:
        """Build the instruction, shrinking the reference sample until it fits.

        Hits give way before flops. There are more of them and they are interchangeable,
        while what did not work is the half of the corpus that says where the line is — a
        batch shown only winners writes pastiche of them. The core documents never give way
        at all: an instruction without the voice or the facts is not a shorter instruction,
        it is a different one.
        """
        hits, flops = self.hit_samples, self.flop_samples
        while True:
            sampled = pool.sample(self.sample_references, hits=hits, flops=flops)
            instruction = build_instruction(
                bundle.model_copy(update={"documents": (*bundle.documents, *sampled)}),
                assignments=assignments,
                country=country,
                language=language,
                history=history,
                account=brief,
            )
            if len(instruction) <= self.max_instruction_chars:
                return sampled, instruction
            if hits > _MIN_HIT_SAMPLES:
                hits -= 1
            elif flops > _MIN_FLOP_SAMPLES:
                flops -= 1
            else:
                # The corpus alone is over the ceiling. Returning the smallest instruction
                # this batch can be written from beats refusing to write it.
                return sampled, instruction

    def _provenance(
        self,
        bundle: CandidateContextBundle,
        instruction: str,
        assignments: tuple[CandidateAssignment, ...],
        sample: tuple[CandidateDocument, ...] = (),
    ) -> CandidateGenerationProvenance:
        """Record what this call read, just before it is spent."""
        return CandidateGenerationProvenance(
            reference_ids=tuple(reference_id(document) for document in sample),
            documents=tuple(
                CandidateContextDocument(
                    relative_path=document.relative_path,
                    size_bytes=len(document.text.encode("utf-8")),
                )
                for document in (*bundle.documents, *sample)
            ),
            model=self.model,
            instruction_chars=len(instruction),
            generated_at=time.time(),
            assigned_domains=tuple(assignment.domain for assignment in assignments),
            batch_size=len(assignments),
        )

    def _draft(
        self,
        call_id: str,
        instruction: str,
        assignments: tuple[CandidateAssignment, ...],
        country: str,
    ) -> tuple[CandidateDraft, ...]:
        answer = self._respond(instruction, call_id)
        try:
            return self._parse(answer, assignments, country)
        except CandidateFormatError as first_failure:
            retry = f"{instruction}\n\n{build_retry_instruction(first_failure.detail)}"
            return self._parse(self._respond(retry, f"{call_id}-retry"), assignments, country)

    def _respond(self, instruction: str, call_id: str) -> JsonValue:
        try:
            return self.client.draft(instruction, call_id=call_id)
        except _PROVIDER_FAILURES as error:
            # The provider's own text is not repeated: it can carry local paths, and the
            # caller chains this exception anyway, so nothing is lost by staying quiet.
            raise CandidateProviderError from error

    @staticmethod
    def _parse(
        answer: JsonValue,
        assignments: tuple[CandidateAssignment, ...],
        country: str,
    ) -> tuple[CandidateDraft, ...]:
        return parse_candidate_drafts(
            answer,
            expected=len(assignments),
            country=country,
            domains=tuple(assignment.domain for assignment in assignments),
        )
