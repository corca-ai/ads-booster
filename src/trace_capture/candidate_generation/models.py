from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from trace_capture.workspace import (
    CandidateCaption,
    CandidateCountry,
    CandidateHypothesis,
    CandidatePrinciple,
    CandidateReference,
    CandidateShootingOrder,
    CandidateTopic,
)


class GenerationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CandidateDraft(GenerationModel):
    """One item of the strict JSON array the generation call must return.

    `appium_prompt` is the free-form image instruction; the store keeps it in the
    candidate's `shooting_order` field.
    """

    topic: CandidateTopic
    country: CandidateCountry
    caption: CandidateCaption
    hypothesis: CandidateHypothesis
    refs_used: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    principles_applied: Annotated[tuple[CandidatePrinciple, ...], Field(max_length=32)] = ()
    appium_prompt: CandidateShootingOrder = ""


class CandidateDocument(GenerationModel):
    relative_path: str
    text: str


class CandidateContextBundle(GenerationModel):
    directory: str
    documents: tuple[CandidateDocument, ...]
