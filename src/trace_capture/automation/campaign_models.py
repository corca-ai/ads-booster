from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, NewType

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from trace_capture.contracts.generation import GenerationReferenceImage as _GenerationReferenceImage
from trace_capture.contracts.generation import PersonaProfile as _PersonaProfile
from trace_capture.contracts.generation import PromotionMaterial as _PromotionMaterial
from trace_capture.contracts.models import DeviceTarget as _DeviceTarget
from trace_capture.workspace import WorkspaceId as _WorkspaceId

if TYPE_CHECKING:
    from trace_capture.contracts.generation import (
        GenerationReferenceImage,
        PersonaProfile,
        PromotionMaterial,
    )
    from trace_capture.contracts.models import DeviceTarget
    from trace_capture.workspace import WorkspaceId

NON_UTC_REFERENCE_DATE: Final = "non_utc_reference_date"
NON_UTC_REFERENCE_DATE_MESSAGE: Final = "campaign reference_date must be UTC"

CampaignId = NewType("CampaignId", str)


@unique
class CampaignState(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"
    COMPLETED = "completed"


class CampaignModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CampaignCreate(CampaignModel):
    workspace_id: WorkspaceId
    name: Annotated[str, Field(min_length=1, max_length=120)]
    persona: PersonaProfile
    promotion_material: PromotionMaterial
    reference_images: tuple[GenerationReferenceImage, ...] = ()
    reference_date: datetime
    device: DeviceTarget
    variation_count: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def require_utc_reference_date(self) -> CampaignCreate:
        if self.reference_date.tzinfo is None or self.reference_date.utcoffset() != UTC.utcoffset(
            self.reference_date
        ):
            raise PydanticCustomError(
                NON_UTC_REFERENCE_DATE,
                NON_UTC_REFERENCE_DATE_MESSAGE,
            )
        return self


class CampaignRecord(CampaignCreate):
    campaign_id: CampaignId
    state: CampaignState
    next_variation: int = Field(ge=0)
    current_queue_id: str | None = None
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


_ = CampaignCreate.model_rebuild(
    _types_namespace={
        "GenerationReferenceImage": _GenerationReferenceImage,
        "PersonaProfile": _PersonaProfile,
        "PromotionMaterial": _PromotionMaterial,
        "DeviceTarget": _DeviceTarget,
        "WorkspaceId": _WorkspaceId,
    }
)
_ = CampaignRecord.model_rebuild(
    _types_namespace={
        "GenerationReferenceImage": _GenerationReferenceImage,
        "PersonaProfile": _PersonaProfile,
        "PromotionMaterial": _PromotionMaterial,
        "DeviceTarget": _DeviceTarget,
        "WorkspaceId": _WorkspaceId,
    }
)
