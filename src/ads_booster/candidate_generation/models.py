from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ads_booster.workspace import (
    CandidateCaption,
    CandidateCountry,
    CandidateHypothesis,
    CandidateImageInputs,
    CandidatePostingSlot,
    CandidatePrinciple,
    CandidateReference,
    CandidateShootingOrder,
    CandidateTopic,
)


class GenerationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CandidateDraft(GenerationModel):
    """One model-authored candidate accepted by the Trace connector tool schema."""

    topic: CandidateTopic
    country: CandidateCountry
    posting_slot: CandidatePostingSlot
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
