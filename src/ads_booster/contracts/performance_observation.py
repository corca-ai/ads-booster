"""Attributed team reports, distinct from verified provider metrics and causal effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_memory import MemoryScope  # noqa: TC001
from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

Count = Annotated[int, Field(strict=True, ge=0, le=10**12)]
Text = Annotated[str, Field(min_length=1, max_length=1000)]


class PerformanceObservation(ContractModel):
    observation_id: Identifier
    scope: MemoryScope
    author_id: Identifier
    source_ref: Text
    source_sha256: Sha256Digest
    channel: Annotated[str, Field(min_length=1, max_length=80)]
    account_id: Annotated[str, Field(min_length=1, max_length=160)]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    publication_ref: Text
    window_start: datetime
    window_end: datetime
    recorded_at: datetime
    views: Count
    likes: Count
    comments: Count
    clicks: Count | None = None
    installs: Count | None = None
    note: Annotated[str, Field(max_length=1000)] = ""
    supersedes: Identifier | None = None
    evidence_status: Literal["human_reported"] = "human_reported"

    @model_validator(mode="after")
    def valid_observation(self) -> Self:
        if not self.scope.work_id:
            message = "performance_observation_requires_work"
            raise ValueError(message)
        for value in (self.window_start, self.window_end, self.recorded_at):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
                message = "performance_observation_requires_utc"
                raise ValueError(message)
        if not self.window_start < self.window_end <= self.recorded_at:
            message = "performance_observation_window_invalid"
            raise ValueError(message)
        if self.observation_id == self.supersedes:
            message = "performance_observation_self_correction"
            raise ValueError(message)
        if any(
            not value.strip()
            for value in (self.source_ref, self.channel, self.account_id, self.publication_ref)
        ):
            message = "performance_observation_attribution_required"
            raise ValueError(message)
        return self


class PerformanceComparison(ContractModel):
    scope: MemoryScope
    observations: Annotated[tuple[PerformanceObservation, ...], Field(min_length=1, max_length=20)]
    comparable: bool
    mismatch_reasons: tuple[str, ...]
    evidence_status: Literal["human_reported"] = "human_reported"
    interpretation: Literal[
        "Individual reported snapshots only; no aggregation or causal inference."
    ] = "Individual reported snapshots only; no aggregation or causal inference."
