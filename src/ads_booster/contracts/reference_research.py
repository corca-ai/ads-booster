"""Source-cited market research contracts for evidence planning."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ads_booster.contracts.models import ContractModel


class ResearchModel(ContractModel):
    pass


class ReferenceSource(ResearchModel):
    source_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    url: Annotated[str, Field(pattern=r"^https://", max_length=2000)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source_type: Literal[
        "threads_post",
        "social_post",
        "article",
        "app_store",
        "official_product",
        "research",
    ]
    summary: Annotated[str, Field(min_length=1, max_length=1500)]
    published_at: Annotated[str | None, Field(max_length=80)] = None
    accessed_at: Annotated[str, Field(min_length=1, max_length=80)]


class MarketObservation(ResearchModel):
    observation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    classification: Literal[
        "saturation",
        "counterevidence",
        "audience_language",
        "format_mechanic",
        "market_context",
    ]
    statement: Annotated[str, Field(min_length=1, max_length=1500)]
    source_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    confidence_basis: Annotated[str, Field(min_length=1, max_length=1000)]


class ReferenceResearchProposal(ResearchModel):
    schema_version: Literal["trace.reference-research-proposal.v1"]
    sources: Annotated[tuple[ReferenceSource, ...], Field(min_length=2, max_length=16)]
    observations: Annotated[tuple[MarketObservation, ...], Field(min_length=2, max_length=24)]
    blind_spots: Annotated[tuple[str, ...], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def validate_source_lineage(self) -> ReferenceResearchProposal:
        source_ids = [source.source_id for source in self.sources]
        if len(set(source_ids)) != len(source_ids):
            message = "research source IDs must be unique"
            raise ValueError(message)
        observation_ids = [item.observation_id for item in self.observations]
        if len(set(observation_ids)) != len(observation_ids):
            message = "research observation IDs must be unique"
            raise ValueError(message)
        known = set(source_ids)
        if any(not set(item.source_ids).issubset(known) for item in self.observations):
            message = "research observation cites an unknown source"
            raise ValueError(message)
        return self
