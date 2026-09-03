from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.canonical import canonical_json
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_portable_run_contract_has_no_adapter_specific_owner() -> None:
    run = AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="run-one",
        tenant_id="trace",
        goal=AgentGoal(objective="Find a stronger launch format", success_criteria=("learn",)),
        budget=AgentBudget(max_tool_calls=5, max_cost_units=20),
        state=AgentRunState.RUNNING,
        revision=2,
        created_at=NOW,
        updated_at=NOW,
    )
    step = AgentStep(
        schema_version="trace.agent-step.v1",
        step_id="step-one",
        run_id=run.run_id,
        sequence=1,
        kind=AgentStepKind.OBSERVE,
        state="completed",
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        occurred_at=NOW,
    )

    assert run.run_id == step.run_id
    assert "cloudflare" not in run.model_dump_json().lower()
    assert "appium" not in run.model_dump_json().lower()
    assert contract_sha256(run) == contract_sha256(run.model_copy())


def test_capability_snapshot_freezes_complete_tool_descriptor() -> None:
    descriptor = _descriptor("capture.native_png", EffectClass.LOCAL_ARTIFACT)
    snapshot = CapabilitySnapshot(
        schema_version="trace.capability-snapshot.v1",
        snapshot_id="snapshot-one",
        run_id="run-one",
        descriptors=(descriptor,),
        created_at=NOW,
    )

    assert snapshot.descriptors[0].input_schema["type"] == "object"
    assert snapshot.descriptors[0].approval_policy.mode == "required"
    assert snapshot.descriptors[0].idempotency.key_scope == "run_tool_input"
    assert snapshot.descriptors[0].reconciliation.mode == "readback"
    assert snapshot.digest == contract_sha256(snapshot)


def test_portable_digest_rejects_cross_runtime_float_ambiguity() -> None:
    with pytest.raises(TypeError, match="portable_json_float_forbidden"):
        _ = contract_sha256({"value": 1.0})


def test_portable_digest_rejects_integer_outside_javascript_safe_range() -> None:
    with pytest.raises(TypeError, match="portable_json_integer_outside_safe_range"):
        _ = contract_sha256({"value": 9_007_199_254_740_992})


def test_portable_digest_uses_javascript_utf16_object_key_order() -> None:
    assert canonical_json({"\ue000": 2, "\U00010000": 1}) == '{"𐀀":1,"":2}'
    assert contract_sha256({"\ue000": 2, "\U00010000": 1}) == contract_sha256(
        {"\U00010000": 1, "\ue000": 2}
    )


def _descriptor(capability_id: str, effect_class: EffectClass) -> ToolDescriptor:
    approval = "none" if effect_class is EffectClass.OBSERVE else "required"
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
        approval_policy=ToolApprovalPolicy(mode=approval),
        cost=ToolCost(worst_case_units=2, unit="operation"),
        readiness=ToolReadiness(ready=True, observed_at=NOW, max_age_seconds=300),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode="none" if effect_class is EffectClass.OBSERVE else "readback",
            lookup_capability_id=(
                None if effect_class is EffectClass.OBSERVE else f"{capability_id}.readback"
            ),
            terminal_dispositions=("succeeded", "failed"),
        ),
    )
