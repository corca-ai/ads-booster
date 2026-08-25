from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from trace_capture.contracts.models import (  # noqa: TC001
    CountryCode,
    DeviceTarget,
    Identifier,
    Locale,
    RelativePath,
    TraceItem,
)

NON_UTC_REFERENCE_DATE: Final = "non_utc_reference_date"


class PersonaProfile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    persona_id: Identifier
    country: CountryCode
    locale: Locale
    age_group: Annotated[str, Field(min_length=1, max_length=40)]
    occupation: Annotated[str, Field(min_length=1, max_length=80)]
    traits: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    interests: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]


class PromotionMaterial(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    promotion_material_id: Identifier
    feature: Annotated[str, Field(min_length=1, max_length=120)]
    concept: Annotated[str, Field(min_length=1, max_length=120)]
    tone: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    trace_items: Annotated[tuple[TraceItem, ...], Field(min_length=1, max_length=8)] | None = None


class GenerationReferenceImage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    reference_id: Identifier
    relative_path: RelativePath
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MarketingContextBundle(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.marketing-context.v1"]
    request_id: Identifier
    campaign_id: Identifier | None = None
    variation_index: int = Field(default=0, ge=0)
    persona: PersonaProfile
    promotion_material: PromotionMaterial
    reference_images: tuple[GenerationReferenceImage, ...] = ()
    reference_date: datetime
    device: DeviceTarget

    @model_validator(mode="after")
    def require_utc_reference_date(self) -> MarketingContextBundle:
        if self.reference_date.tzinfo is None or self.reference_date.utcoffset() != UTC.utcoffset(
            self.reference_date
        ):
            raise PydanticCustomError(
                NON_UTC_REFERENCE_DATE,
                "marketing context reference_date must be UTC",
            )
        return self
