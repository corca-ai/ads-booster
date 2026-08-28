from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ads_booster.workspace import (
    CandidateCaption,
    CandidateCountry,
    CandidateHypothesis,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidatePostingSlot,
    CandidatePrinciple,
    CandidateReference,
    CandidateShootingOrder,
    CandidateTopic,
)

if TYPE_CHECKING:
    from ads_booster.workspace import CandidateRecord


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """What one generation batch produced, including the calls that produced nothing.

    A batch is now one provider call per candidate, so partial success is the normal
    outcome rather than an edge case: two captions and one timeout is two captions worth
    keeping. `failures` is carried out rather than logged away because the person who
    pressed the button asked for a number of candidates and has to be told they got fewer.
    """

    records: tuple[CandidateRecord, ...]
    failures: int = 0


class GenerationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CandidateDraft(GenerationModel):
    """One model-authored candidate, from either generator.

    The script engine binds `persona_domain` to the coverage assignment it made before the
    call and leaves `posting_slot` at its default; the Trace connector chooses the slot from
    its context and leaves the domain unset. Both fields therefore carry a default so one
    draft type can serve both paths.
    """

    topic: CandidateTopic
    country: CandidateCountry
    posting_slot: CandidatePostingSlot = CandidatePostingSlot.MANUAL
    # Assigned by the caller before the call, not chosen by the model: coverage only means
    # something if the label comes from the fixed vocabulary the counts are kept over.
    persona_domain: CandidatePersonaDomain | None = None
    caption: CandidateCaption
    hypothesis: CandidateHypothesis
    image_inputs: CandidateImageInputs
    refs_used: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    principles_applied: Annotated[tuple[CandidatePrinciple, ...], Field(max_length=32)] = ()
    appium_prompt: CandidateShootingOrder = ""


class CandidateDocument(GenerationModel):
    relative_path: str
    text: str


class CandidateContextBundle(GenerationModel):
    directory: str
    documents: tuple[CandidateDocument, ...]
