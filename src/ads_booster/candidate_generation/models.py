from __future__ import annotations

from typing import Annotated, ClassVar

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
