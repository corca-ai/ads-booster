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
    """One model-authored candidate, before anything downstream has accepted it.

    Every bound here is the tighter of what the instruction asks for and what the hosted
    control plane will accept, so a draft that validates is a draft that can be delivered.
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
    # At least one principle, because the delivery contract requires one and a draft that
    # cites nothing cannot say which part of the corpus it was reasoning from.
    principles_applied: Annotated[
        tuple[CandidatePrinciple, ...], Field(min_length=1, max_length=16)
    ]
    appium_prompt: CandidateShootingOrder = ""


class CandidateDocument(GenerationModel):
    relative_path: str
    text: str


class CandidateContextBundle(GenerationModel):
    directory: str
    documents: tuple[CandidateDocument, ...]
