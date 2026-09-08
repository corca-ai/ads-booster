from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.knowledge_context import EditorialContextBlock, EditorialContextRole
from ads_booster.contracts.knowledge_selection import VoiceStatus
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


class CandidateEditorialContext(GenerationModel):
    mode: Literal["knowledge_context_v1"] = "knowledge_context_v1"
    voice_status: VoiceStatus
    blocks: Annotated[tuple[EditorialContextBlock, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def require_voice_shape(self) -> Self:
        if (
            self.voice_status not in {VoiceStatus.CONFIGURED, VoiceStatus.VOICE_UNCONFIGURED}
            or (self.voice_status is VoiceStatus.CONFIGURED and not self.blocks)
            or (
                self.voice_status is VoiceStatus.VOICE_UNCONFIGURED
                and any(block.role is not EditorialContextRole.CONSTRAINT for block in self.blocks)
            )
        ):
            code = "candidate_editorial_context_invalid"
            raise PydanticCustomError(
                code,
                "configured voice requires blocks; unconfigured voice permits only constraints",
            )
        return self
