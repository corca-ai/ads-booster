"""Governed, tenant-scoped customer intelligence for marketing-agent planning.

These contracts model normalized observations and approved campaign context rather than raw-text or
connector-record fields. A context snapshot freezes the small planner projection that a campaign may
consume; it is not mutable agent memory. ``retention_until`` is this v1's use-eligibility deadline,
not a physical-deletion service-level agreement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

_SIGNAL_LIFETIME_UNORDERED: Final = "customer signal lifetime is not ordered"
_SIGNAL_CAVEATS_NOT_UNIQUE: Final = "customer signal caveats must be unique"
_CONTEXT_EXPIRES_BEFORE_APPROVAL: Final = "marketing context must expire after approval"
_CONTEXT_SIGNAL_IDS_NOT_UNIQUE: Final = "marketing context signal IDs must be unique"
_CONTEXT_OUTLIVES_SIGNAL: Final = "marketing context outlives a customer signal freshness window"
_CONTEXT_GUARDRAILS_NOT_UNIQUE: Final = "marketing context brand guardrails must be unique"
_CONTEXT_AUDIENCE_NOT_UNIQUE: Final = "marketing context audience items must be unique"
_CONTEXT_CHANNEL_POLICIES_NOT_UNIQUE: Final = "marketing context channel policy IDs must be unique"
_PROJECTION_SIGNAL_IDS_NOT_UNIQUE: Final = "marketing context projection signal IDs must be unique"
_PROJECTION_OUTLIVES_SIGNAL: Final = (
    "marketing context projection outlives a customer signal freshness window"
)


@unique
class CustomerSignalSourceKind(StrEnum):
    """The first source is deliberately a reviewed manual normalization, not a connector."""

    MANUAL_NORMALIZED = "manual_normalized"


@unique
class CustomerSignalKind(StrEnum):
    NEED = "need"
    OBJECTION = "objection"
    DESIRED_OUTCOME = "desired_outcome"
    AUDIENCE_LANGUAGE = "audience_language"
    BEHAVIOR = "behavior"


@unique
class CustomerSignalConsentStatus(StrEnum):
    """A signal cannot enter a snapshot unless its limited-use consent is confirmed."""

    CONFIRMED = "confirmed"


class CustomerSignal(ContractModel):
    """One immutable, normalized customer observation with privacy and freshness bounds."""

    schema_version: Literal["trace.customer-signal.v1"]
    signal_id: Identifier
    account_id: Identifier
    source_kind: CustomerSignalSourceKind
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    source_sha256: Sha256Digest
    audience_segment_id: Identifier
    kind: CustomerSignalKind
    summary: Annotated[str, Field(min_length=1, max_length=1200)]
    caveats: Annotated[tuple[str, ...], Field(max_length=8)] = ()
    confidence_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    consent_status: CustomerSignalConsentStatus
    observed_at: datetime
    fresh_until: datetime
    retention_until: datetime

    @model_validator(mode="after")
    def require_governed_lifetime(self) -> Self:
        _require_utc(self.observed_at, field="observed_at")
        _require_utc(self.fresh_until, field="fresh_until")
        _require_utc(self.retention_until, field="retention_until")
        if not self.observed_at <= self.fresh_until <= self.retention_until:
            raise ValueError(_SIGNAL_LIFETIME_UNORDERED)
        if len(set(self.caveats)) != len(self.caveats):
            raise ValueError(_SIGNAL_CAVEATS_NOT_UNIQUE)
        return self


class CustomerSignalPlanningProjection(ContractModel):
    """Allowlisted signal shape; source references and consent metadata stay outside the planner."""

    schema_version: Literal["trace.customer-signal-projection.v1"]
    signal_id: Identifier
    signal_sha256: Sha256Digest
    audience_segment_id: Identifier
    kind: CustomerSignalKind
    summary: Annotated[str, Field(min_length=1, max_length=1200)]
    caveats: Annotated[tuple[str, ...], Field(max_length=8)] = ()
    confidence_basis_points: Annotated[int, Field(ge=0, le=10_000)]
    observed_at: datetime
    fresh_until: datetime

    @classmethod
    def from_signal(cls, signal: CustomerSignal, *, signal_sha256: str) -> Self:
        """Project a reviewed signal without exposing origin or consent to the planner."""
        return cls(
            schema_version="trace.customer-signal-projection.v1",
            signal_id=signal.signal_id,
            signal_sha256=signal_sha256,
            audience_segment_id=signal.audience_segment_id,
            kind=signal.kind,
            summary=signal.summary,
            caveats=signal.caveats,
            confidence_basis_points=signal.confidence_basis_points,
            observed_at=signal.observed_at,
            fresh_until=signal.fresh_until,
        )


class MarketingContextSnapshot(ContractModel):
    """One human-approved, immutable context selection for a tenant campaign."""

    schema_version: Literal["trace.marketing-context.v1"]
    snapshot_id: Identifier
    account_id: Identifier
    brand_guardrails: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    audience_context: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    channel_policy_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    customer_signals: Annotated[
        tuple[CustomerSignalPlanningProjection, ...], Field(min_length=1, max_length=24)
    ]
    approved_by: Identifier
    approved_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def require_approved_unique_fresh_context(self) -> Self:
        _require_utc(self.approved_at, field="approved_at")
        _require_utc(self.expires_at, field="expires_at")
        if self.approved_at >= self.expires_at:
            raise ValueError(_CONTEXT_EXPIRES_BEFORE_APPROVAL)
        signal_ids = tuple(signal.signal_id for signal in self.customer_signals)
        if len(set(signal_ids)) != len(signal_ids):
            raise ValueError(_CONTEXT_SIGNAL_IDS_NOT_UNIQUE)
        if any(signal.fresh_until < self.expires_at for signal in self.customer_signals):
            raise ValueError(_CONTEXT_OUTLIVES_SIGNAL)
        if len(set(self.brand_guardrails)) != len(self.brand_guardrails):
            raise ValueError(_CONTEXT_GUARDRAILS_NOT_UNIQUE)
        if len(set(self.audience_context)) != len(self.audience_context):
            raise ValueError(_CONTEXT_AUDIENCE_NOT_UNIQUE)
        if len(set(self.channel_policy_ids)) != len(self.channel_policy_ids):
            raise ValueError(_CONTEXT_CHANNEL_POLICIES_NOT_UNIQUE)
        return self


class MarketingContextPlanningProjection(ContractModel):
    """Frozen planner input selected from an approved ``MarketingContextSnapshot``."""

    schema_version: Literal["trace.marketing-context-projection.v1"]
    snapshot_id: Identifier
    snapshot_sha256: Sha256Digest
    account_id: Identifier
    brand_guardrails: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    audience_context: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    channel_policy_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    customer_signals: Annotated[
        tuple[CustomerSignalPlanningProjection, ...], Field(min_length=1, max_length=24)
    ]
    expires_at: datetime

    @model_validator(mode="after")
    def require_unique_signals(self) -> Self:
        _require_utc(self.expires_at, field="expires_at")
        signal_ids = tuple(signal.signal_id for signal in self.customer_signals)
        if len(set(signal_ids)) != len(signal_ids):
            raise ValueError(_PROJECTION_SIGNAL_IDS_NOT_UNIQUE)
        if any(signal.fresh_until < self.expires_at for signal in self.customer_signals):
            raise ValueError(_PROJECTION_OUTLIVES_SIGNAL)
        return self

    @classmethod
    def from_snapshot(cls, snapshot: MarketingContextSnapshot, *, snapshot_sha256: str) -> Self:
        """Keep only approved planner-safe context; provenance remains in the hosted ledger."""
        return cls(
            schema_version="trace.marketing-context-projection.v1",
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot_sha256,
            account_id=snapshot.account_id,
            brand_guardrails=snapshot.brand_guardrails,
            audience_context=snapshot.audience_context,
            channel_policy_ids=snapshot.channel_policy_ids,
            customer_signals=snapshot.customer_signals,
            expires_at=snapshot.expires_at,
        )


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        message = f"{field} must be UTC"
        raise ValueError(message)
