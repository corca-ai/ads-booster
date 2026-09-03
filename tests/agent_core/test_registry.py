from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)
from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_snapshot_excludes_unready_appium_without_blocking_research() -> None:
    registry = ToolRegistry(
        (
            _descriptor("research.web", EffectClass.OBSERVE, ready=True, cost=1),
            _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=False, cost=5),
        )
    )

    snapshot = registry.snapshot_for_plan(
        snapshot_id="snapshot-one",
        run_id="run-one",
        remaining_tool_calls=2,
        remaining_cost_units=10,
        policy=CapabilityPolicy(),
        now=NOW,
    )

    assert tuple(item.capability_id for item in snapshot.descriptors) == ("research.web",)


def test_snapshot_applies_installation_policy_and_remaining_budget() -> None:
    registry = ToolRegistry(
        (
            _descriptor("research.web", EffectClass.OBSERVE, ready=True, cost=2),
            _descriptor("creative.image", EffectClass.LOCAL_ARTIFACT, ready=True, cost=8),
            _descriptor("publish.threads", EffectClass.EXTERNAL, ready=True, cost=3),
        )
    )

    snapshot = registry.snapshot_for_plan(
        snapshot_id="snapshot-two",
        run_id="run-two",
        remaining_tool_calls=2,
        remaining_cost_units=4,
        policy=CapabilityPolicy(denied_capability_ids=("publish.threads",)),
        now=NOW,
    )

    assert tuple(item.capability_id for item in snapshot.descriptors) == ("research.web",)


def test_registry_rejects_duplicate_capability_versions() -> None:
    descriptor = _descriptor("research.web", EffectClass.OBSERVE, ready=True, cost=1)

    with pytest.raises(ValueError, match="duplicate_tool_descriptor"):
        _ = ToolRegistry((descriptor, descriptor))


def test_snapshot_excludes_stale_readiness_and_zero_call_budget() -> None:
    registry = ToolRegistry((_descriptor("research.web", EffectClass.OBSERVE, ready=True, cost=1),))

    stale = registry.snapshot_for_plan(
        snapshot_id="snapshot-stale",
        run_id="run-stale",
        remaining_tool_calls=1,
        remaining_cost_units=2,
        policy=CapabilityPolicy(),
        now=NOW.replace(minute=6),
    )
    exhausted = registry.snapshot_for_plan(
        snapshot_id="snapshot-exhausted",
        run_id="run-exhausted",
        remaining_tool_calls=0,
        remaining_cost_units=2,
        policy=CapabilityPolicy(),
        now=NOW,
    )

    assert stale.descriptors == ()
    assert exhausted.descriptors == ()


def test_current_dispatch_accepts_a_newer_healthy_heartbeat_for_same_tool() -> None:
    frozen = _descriptor("research.web", EffectClass.OBSERVE, ready=True, cost=1)
    refreshed = frozen.model_copy(
        update={
            "readiness": frozen.readiness.model_copy(
                update={"observed_at": NOW.replace(second=30)}
            )
        }
    )
    registry = ToolRegistry((refreshed,))

    assert (
        registry.require_current_dispatch(
            frozen,
            policy=CapabilityPolicy(),
            now=NOW.replace(minute=1),
        )
        == refreshed
    )


def _descriptor(
    capability_id: str,
    effect_class: EffectClass,
    *,
    ready: bool,
    cost: int,
) -> ToolDescriptor:
    schema: JsonObject = {"type": "object"}
    schema_sha256 = contract_sha256(schema)
    return ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id=capability_id,
        version="1",
        owner="test.adapter",
        installation_id="test-installation",
        input_schema=schema,
        input_schema_sha256=schema_sha256,
        output_schema=schema,
        output_schema_sha256=schema_sha256,
        config_schema=schema,
        config_schema_sha256=schema_sha256,
        receipt_schema=schema,
        receipt_schema_sha256=schema_sha256,
        credential_boundary="none",
        effect_class=effect_class,
        approval_policy=ToolApprovalPolicy(
            mode="none" if effect_class is EffectClass.OBSERVE else "required"
        ),
        cost=ToolCost(worst_case_units=cost, unit="operation"),
        readiness=ToolReadiness(
            ready=ready,
            reason_code=None if ready else "worker_unavailable",
            observed_at=NOW,
            max_age_seconds=300,
        ),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode="none" if effect_class is EffectClass.OBSERVE else "readback",
            lookup_capability_id=(
                None if effect_class is EffectClass.OBSERVE else f"{capability_id}.readback"
            ),
            terminal_dispositions=("succeeded", "failed"),
        ),
    )
