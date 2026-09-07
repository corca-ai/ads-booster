"""Attributed human work measurements; neither runtime cost nor causal performance proof."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_memory import MemoryScope  # noqa: TC001 - runtime schema
from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

WorkPhase = Literal["research", "planning", "production", "localization", "revision", "review"]


class ObservedAsset(ContractModel):
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    sha256: Sha256Digest


class WorkObservation(ContractModel):
    observation_id: Identifier
    scope: MemoryScope
    author_id: Identifier
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    evidence_status: Literal["human_reported"] = "human_reported"
    note: Annotated[str, Field(max_length=1000)] = ""
    phase: WorkPhase
    elapsed_minutes: Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)]
    revision_count: Annotated[int, Field(ge=0, le=10000)] = 0
    window_kind: Literal["reported_interval", "report_time"] = "reported_interval"
    window_start: datetime
    window_end: datetime
    recorded_at: datetime
    asset: ObservedAsset | None = None
    locale: Annotated[
        str, Field(max_length=35, pattern=r"^(?:[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)?$")
    ] = ""
    supersedes: Identifier | None = None

    @model_validator(mode="after")
    def valid_observation(self) -> Self:
        for value in (self.window_start, self.window_end, self.recorded_at):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
                message = "work_observation_requires_utc"
                raise ValueError(message)
        if not self.scope.work_id:
            message = "work_observation_requires_work"
            raise ValueError(message)
        if self.window_start > self.window_end or self.window_end > self.recorded_at:
            message = "work_observation_window_invalid"
            raise ValueError(message)
        if self.window_kind == "report_time" and self.window_start != self.window_end:
            message = "work_report_time_requires_equal_endpoints"
            raise ValueError(message)
        if self.observation_id == self.supersedes:
            message = "work_observation_self_correction"
            raise ValueError(message)
        return self


class WorkSummary(ContractModel):
    scope: MemoryScope
    observations: tuple[WorkObservation, ...]
    elapsed_minutes: float
    revision_count: int
    evidence_status: Literal["human_reported"] = "human_reported"
    interpretation: Literal[
        "Reported effort only; overlapping reports may overlap in time. No causal or cost estimate."
    ] = "Reported effort only; overlapping reports may overlap in time. No causal or cost estimate."
