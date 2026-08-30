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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import Lock
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
    CaptionForm,
    assign_caption_forms,
    build_instruction,
    build_retry_instruction,
)
from ads_booster.candidate_generation.parsing import parse_candidate_drafts
from ads_booster.candidate_generation.topics import duplicate_indexes
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
# How many Codex turns a batch may have in flight. Four is a working default rather than a
# measured limit: nothing in this repository serialises a generation turn, but the account
# behind the Codex CLI may rate-limit, and a throttled turn comes back as an ordinary
# provider failure — a smaller batch, not a broken one. Set it to 1 to run in order.
DEFAULT_MAX_WORKERS: Final = 4


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


def assign_interests(interests: Sequence[str], count: int) -> tuple[str | None, ...]:
    """Give each candidate its own subject axis out of the account's interests.

    This is the separation that survives running the calls at the same time. A parallel
    batch cannot show one call what the others wrote, so whatever keeps the candidates apart
    has to be decided before any of them is sent — and an account's own interests are the
    one list that is already both specific to that person and plural.

    Cycling rather than failing when there are fewer interests than candidates: two
    candidates on the same axis is a weaker guarantee than two on different ones, not a
    broken batch, and the topic check afterwards is what catches it if they collide. An
    account with no interests recorded gets no axis at all, which is honest — there is
    nothing to divide it by.
    """
    if not interests:
        return (None,) * count
    return tuple(interests[index % len(interests)] for index in range(count))


@dataclass(slots=True)
class WrittenTopics:
    """The topics a call must not repeat: the stored history plus this batch so far.

    Only a batch that runs its calls in order can fill this in as it goes; a parallel batch
    starts every call from the same stored history and relies on the axis and form each was
    assigned instead. The regeneration pass is the other reader: a candidate rewritten
    because it collided is shown exactly what it collided with.

    Guarded by a lock because a sequential batch is not the only caller any more, and a list
    that is only sometimes shared is the kind that is only sometimes wrong.
    """

    stored: tuple[CandidateHistoryEntry, ...] = ()
    fresh: list[CandidateHistoryEntry] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def entries(self) -> tuple[CandidateHistoryEntry, ...]:
        with self.lock:
            return (*self.fresh, *self.stored)

    def add(self, persona_domain: CandidatePersonaDomain | None, topic: str) -> None:
        with self.lock:
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
    # The subject axis this call was assigned, when the account had interests to divide by.
    assigned_interest: str | None = None
    # Set when this candidate restated one an earlier position had already claimed and was
    # asked again, and when it still restated it afterwards. A reviewer looking at two
    # similar captions should be able to see that the system noticed rather than guess.
    regenerated: bool = False
    duplicate_topic: bool = False


@dataclass(frozen=True, slots=True)
class CandidateDraftBatch:
    """What one batch of provider calls produced, including the calls that produced nothing.

    Partial success is the normal outcome — three captions and one timeout is three captions
    worth keeping — so `failures` travels with the drafts rather than being logged away.
    """

    drafts: tuple[DraftedCandidate, ...]
    failures: int = 0
    # Whether the calls actually ran at the same time, which decides what was carrying the
    # separation between them.
    parallel: bool = False


@dataclass(frozen=True, slots=True)
class _CandidateCall:
    """Everything one candidate's provider call needs, decided before the call goes out."""

    index: int
    bundle: CandidateContextBundle
    pool: ReferencePool
    domain: CandidatePersonaDomain
    form: CaptionForm
    interest: str | None
    brief: CandidateAccountBrief | None
    country: str
    language: str
    written: WrittenTopics
    # Distinguishes the regeneration turn from the first one, so the two never land in the
    # same Codex workspace.
    attempt: int = 0

    @property
    def call_id(self) -> str:
        """This turn's place, unique within the batch including its retry and rewrite."""
        return f"{self.index:02d}" if self.attempt == 0 else f"{self.index:02d}-{self.attempt}"


@dataclass(frozen=True, slots=True)
class _WrittenCall:
    """One candidate and the call that produced it, kept together for the rewrite pass."""

    call: _CandidateCall
    drafted: DraftedCandidate


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
    # How many turns run at once. One runs the batch in order, which is the only mode where
    # a call can be shown what the calls before it wrote.
    max_workers: int = DEFAULT_MAX_WORKERS

    def draft(  # noqa: PLR0913 - each argument is one independent input to the batch.
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
        """Write one batch as one provider call per candidate.

        A single call for the whole batch had to keep every candidate distinct by itself,
        and it read the same context every time. One call per candidate lets each draw its
        own reference sample, so the batch collectively sees more of the corpus than any
        one instruction could carry.

        Everything that keeps the candidates apart is decided before the calls go out: a
        domain each, a caption form each, a subject axis each. That is what makes running
        them at the same time safe — the alternative guard, showing a call what the batch
        has already written, is the one thing parallelism cannot do, and it was costing the
        batch its wall clock to buy something the assignments already provide.

        What parallelism does cost is certainty, so the topics are compared afterwards and
        a candidate that restated an earlier one is asked again — that once, in order, with
        the topic it collided with in front of it.

        `bundle` and `pool` are handed in already loaded rather than read here, so a caller
        can find out that its corpus is unusable before it spends anything on a provider —
        a missing document is an ordinary failed task, and one discovered mid-batch is not.
        """
        forms = assign_caption_forms(len(domains))
        axes = assign_interests(interests, len(domains))
        written = WrittenTopics(history)
        calls = [
            _CandidateCall(
                index=index,
                bundle=bundle,
                pool=pool,
                domain=domain,
                form=forms[index],
                interest=axes[index],
                brief=brief,
                country=country,
                language=language,
                written=written,
            )
            for index, domain in enumerate(domains)
        ]
        parallel = self._workers(len(calls)) > 1
        written_calls, failures = self._parallel(calls) if parallel else self._sequential(calls)
        if not written_calls:
            raise failures[0]
        return CandidateDraftBatch(
            drafts=self._resolve_duplicates(written_calls),
            failures=len(failures),
            parallel=parallel,
        )

    def _workers(self, calls: int) -> int:
        return max(1, min(self.max_workers, calls))

    def _sequential(
        self,
        calls: Sequence[_CandidateCall],
    ) -> tuple[list[_WrittenCall], list[CandidateGenerationError]]:
        """Run the batch in order, each call shown what the ones before it wrote."""
        written_calls: list[_WrittenCall] = []
        failures: list[CandidateGenerationError] = []
        for call in calls:
            try:
                drafted = self._one(call)
            except CandidateGenerationError as error:
                failures.append(error)
                continue
            call.written.add(drafted.draft.persona_domain, drafted.draft.topic)
            written_calls.append(_WrittenCall(call=call, drafted=drafted))
        return written_calls, failures

    def _parallel(
        self,
        calls: Sequence[_CandidateCall],
    ) -> tuple[list[_WrittenCall], list[CandidateGenerationError]]:
        """Run the batch at once, collecting results in the order they were assigned.

        Reading the futures in submission order rather than as they complete is what keeps
        the batch reproducible: which turn finishes first is a fact about the machine, and
        it must not decide which candidate is treated as the original when two collide.
        """
        written_calls: list[_WrittenCall] = []
        failures: list[CandidateGenerationError] = []
        with ThreadPoolExecutor(
            max_workers=self._workers(len(calls)),
            thread_name_prefix="trace-candidate",
        ) as workers:
            futures = [(call, workers.submit(self._one, call)) for call in calls]
            for call, future in futures:
                try:
                    written_calls.append(_WrittenCall(call=call, drafted=future.result()))
                except CandidateGenerationError as error:
                    failures.append(error)
        return written_calls, failures

    def _resolve_duplicates(
        self,
        written_calls: Sequence[_WrittenCall],
    ) -> tuple[DraftedCandidate, ...]:
        """Ask again for any candidate that restated one an earlier position already wrote.

        Only once, and only for the later one: the earliest occurrence keeps the topic, so
        the outcome depends on the order the batch was assigned rather than on which turn
        happened to return first. A regeneration that collides again is kept and labelled —
        two similar captions a reviewer can see were flagged beat one candidate silently
        thrown away.
        """
        resolved = [written.drafted for written in written_calls]
        for position in duplicate_indexes([drafted.draft.topic for drafted in resolved]):
            original = resolved[position]
            try:
                rewritten = self._one(
                    replace(
                        written_calls[position].call,
                        written=self._history_beside(resolved, position),
                        attempt=1,
                    )
                )
            except CandidateGenerationError:
                # The first draft is still a candidate. Losing it because the second attempt
                # timed out would trade a flagged duplicate for nothing at all.
                resolved[position] = replace(original, regenerated=True, duplicate_topic=True)
                continue
            resolved[position] = replace(rewritten, regenerated=True)
        # Recomputed rather than assumed: a regeneration can land on a third candidate's
        # topic, and one that did is still a duplicate.
        for position in duplicate_indexes([drafted.draft.topic for drafted in resolved]):
            resolved[position] = replace(resolved[position], duplicate_topic=True)
        return tuple(resolved)

    @staticmethod
    def _history_beside(drafts: Sequence[DraftedCandidate], position: int) -> WrittenTopics:
        """Everything the batch wrote except the candidate being asked again."""
        return WrittenTopics(
            tuple(
                CandidateHistoryEntry(
                    persona_domain=other.draft.persona_domain,
                    topic=other.draft.topic,
                )
                for index, other in enumerate(drafts)
                if index != position
            )
        )

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
            interest=call.interest,
        )
        provenance = self._provenance(bundle, instruction, (call.domain,), sample)
        draft = self._draft(call, instruction)
        return DraftedCandidate(
            draft=draft,
            provenance=provenance,
            caption_form=call.form,
            assigned_interest=call.interest,
        )

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
        answer = self._respond(instruction, call.call_id)
        try:
            return self._parse(answer, call)
        except CandidateFormatError as first_failure:
            retry = f"{instruction}\n\n{build_retry_instruction(first_failure.detail)}"
            return self._parse(self._respond(retry, f"{call.call_id}-retry"), call)

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
