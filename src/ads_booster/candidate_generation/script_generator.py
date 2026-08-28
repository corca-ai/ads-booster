"""Single-call candidate generation from the assembled Korean context documents.

This is the "script assembly" generator: no tool loop and no web search. Every context
document is read into one instruction, the model answers with a strict JSON array, and one
failed validation is retried once before the run gives up without writing anything. The
Agent-kernel connector in `agent_generator` remains available for the tool-loop path; this
module is what the Web generate route runs.

The provider calls themselves live in `draft_engine`; what is left here is the workspace
half — which domains this batch covers, what it has already written, and where the rows go.
The Mac worker runs the engine on its own and hands the drafts to the hosted control plane
instead, which is what makes a hosted batch identical to a local one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.candidate_generation.draft_engine import (
    CandidateDraftEngine,
    WrittenTopics,
)
from ads_booster.candidate_generation.models import CandidateBatch, CandidateDocument
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidateCreate,
    CandidatePersonaDomain,
    CandidateSource,
    MarketingAccountId,
    MarketingAccountRecord,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.context_source import (
        CandidateContextSource,
        CandidateReferenceSource,
    )
    from ads_booster.candidate_generation.models import CandidateDraft
    from ads_booster.workspace import (
        CandidateGenerationProvenance,
        CandidateHistoryEntry,
        CandidateRecord,
        WorkspaceId,
    )

DEFAULT_CANDIDATE_COUNT: Final = 3
DEFAULT_COUNTRY: Final = "KR"
DEFAULT_HISTORY_LIMIT: Final = 15

__all__ = [
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_COUNTRY",
    "DEFAULT_HISTORY_LIMIT",
    "CandidateModelSource",
    "CandidateWriter",
    "ScriptCandidateGenerator",
    "WrittenTopics",
    "assign_domains",
    "default_domain_shuffle",
]


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
    """Decides what one batch covers, runs the draft engine, and stores what came back.

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

    @property
    def engine(self) -> CandidateDraftEngine:
        """The provider half of this generator, composed from the same fields.

        Built per access rather than stored: every field it carries is frozen, so the
        object is a view over this one rather than state that could drift from it.
        """
        return CandidateDraftEngine(
            models=self.models,
            context_source=self.context_source,
            references=self.references,
            model=self.model,
            sample_references=self.sample_references,
        )

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
        account: MarketingAccountRecord | None = None,
    ) -> CandidateBatch:
        """Write one batch of candidates into the workspace.

        With an account the batch is that one person writing about different things, so
        coverage stops choosing domains and the account's own domain applies to all of
        them. The history is scoped to that account for the same reason: what one account
        has already written is a fact about that account.
        """
        del run_context
        # The corpus follows the account, not a constant: a batch is grounded in the posts
        # its own country's readers responded to. Without an account the workspace-wide
        # default stands in.
        brief = None if account is None else CandidateAccountBrief.of(account)
        account_id = None if account is None else account.account_id
        domains = (
            (brief.domain,) * self.count
            if brief is not None
            else assign_domains(
                self.store.count_candidate_domains(workspace_id), self.count, self.shuffle
            )
        )
        batch = self.engine.draft(
            corpus_country=self.country if account is None else account.country,
            draft_country=self.country,
            domains=domains,
            brief=brief,
            history=self.store.recent_candidate_history(
                workspace_id, self.history_limit, account_id=account_id
            ),
        )
        records = [
            self.store.create_candidate(
                self._create(workspace_id, generated.draft, generated.provenance, account_id)
            )
            for generated in batch.drafts
        ]
        return CandidateBatch(records=tuple(records), failures=batch.failures)

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
