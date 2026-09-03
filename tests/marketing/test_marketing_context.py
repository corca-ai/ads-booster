"""Contract regression tests for governed marketing context and customer signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.contracts.marketing_context import (
    CustomerSignal,
    CustomerSignalConsentStatus,
    CustomerSignalKind,
    CustomerSignalPlanningProjection,
    CustomerSignalSourceKind,
    MarketingContextPlanningProjection,
    MarketingContextSnapshot,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _signal(
    *,
    signal_id: str = "signal-1",
    retention_until: datetime | None = None,
) -> CustomerSignal:
    return CustomerSignal(
        schema_version="trace.customer-signal.v1",
        signal_id=signal_id,
        account_id="trace_kr",
        source_kind=CustomerSignalSourceKind.MANUAL_NORMALIZED,
        source_ref="interview-batch-2026-09",
        source_sha256="a" * 64,
        audience_segment_id="ios-character-fans",
        kind=CustomerSignalKind.DESIRED_OUTCOME,
        summary="People want a familiar character to make daily planning feel personal.",
        caveats=("This is a small qualitative sample.",),
        confidence_basis_points=6_500,
        consent_status=CustomerSignalConsentStatus.CONFIRMED,
        observed_at=NOW,
        fresh_until=NOW + timedelta(days=14),
        retention_until=retention_until or NOW + timedelta(days=30),
    )


def _snapshot(*, signals: tuple[CustomerSignalPlanningProjection, ...]) -> MarketingContextSnapshot:
    return MarketingContextSnapshot(
        schema_version="trace.marketing-context.v1",
        snapshot_id="context-1",
        account_id="trace_kr",
        brand_guardrails=("Show the product truth before emotional framing.",),
        audience_context=("iPhone users who personalize lock screens",),
        channel_policy_ids=("threads-organic",),
        customer_signals=signals,
        approved_by="reviewer-1",
        approved_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(days=7),
    )


def test_snapshot_freezes_only_the_planner_safe_customer_signal_projection() -> None:
    signal = _signal()
    projection = CustomerSignalPlanningProjection.from_signal(
        signal,
        signal_sha256=contract_sha256(signal),
    )
    snapshot = _snapshot(signals=(projection,))

    planner_context = MarketingContextPlanningProjection.from_snapshot(
        snapshot,
        snapshot_sha256=contract_sha256(snapshot),
    )

    payload = planner_context.model_dump(mode="json")
    assert payload["customer_signals"][0]["signal_id"] == signal.signal_id
    assert "source_ref" not in payload["customer_signals"][0]
    assert "source_sha256" not in payload["customer_signals"][0]
    assert "consent_status" not in payload["customer_signals"][0]
    assert payload["customer_signals"][0]["caveats"] == list(signal.caveats)


def test_signal_rejects_expired_or_unordered_retention_window() -> None:
    with pytest.raises(ValueError, match="lifetime is not ordered"):
        _ = _signal(retention_until=NOW + timedelta(days=7))


def test_snapshot_rejects_duplicate_or_stale_signals() -> None:
    signal = _signal()
    projection = CustomerSignalPlanningProjection.from_signal(
        signal,
        signal_sha256=contract_sha256(signal),
    )
    with pytest.raises(ValueError, match="signal IDs must be unique"):
        _ = _snapshot(signals=(projection, projection))

    stale = projection.model_copy(update={"fresh_until": NOW + timedelta(days=3)})
    with pytest.raises(ValueError, match="outlives a customer signal freshness window"):
        _ = _snapshot(signals=(stale,))
