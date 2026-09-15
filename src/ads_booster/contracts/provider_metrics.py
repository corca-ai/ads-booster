from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest


@unique
class ProviderMetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class ProviderMetricSnapshot(ContractModel):
    schema_version: Literal["trace.provider-metric-snapshot.v1"] = (
        "trace.provider-metric-snapshot.v1"
    )
    snapshot_id: Identifier
    provider: Literal["threads"]
    workspace_id: Identifier
    connection_id: Identifier
    provider_account_id: Annotated[str, Field(min_length=1, max_length=160)]
    subject_kind: Literal["account", "post"]
    subject_id: Annotated[str, Field(min_length=1, max_length=160)]
    metric: Annotated[str, Field(min_length=1, max_length=80)]
    period: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[int, Field(ge=0)] | None
    availability: ProviderMetricAvailability
    observed_at: datetime
    provider_api_version: Annotated[str, Field(min_length=1, max_length=80)]
    source_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_snapshot_shape(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(None):
            raise PydanticCustomError(
                "provider_metric_time_requires_utc", "observed_at must be UTC"
            )
        if (self.availability is ProviderMetricAvailability.AVAILABLE) != (
            self.value is not None
        ):
            raise PydanticCustomError(
                "provider_metric_availability_mismatch",
                "available metrics require values and other states cannot have values",
            )
        return self


class ProviderMetricChange(ContractModel):
    previous_snapshot_id: Identifier
    current_snapshot_id: Identifier
    comparable: bool
    change: int | None
    reason: Literal[
        "comparable",
        "identity_mismatch",
        "definition_mismatch",
        "unavailable",
        "counter_decreased",
        "observation_order_invalid",
    ]


__all__ = [
    "ProviderMetricAvailability",
    "ProviderMetricChange",
    "ProviderMetricSnapshot",
]
