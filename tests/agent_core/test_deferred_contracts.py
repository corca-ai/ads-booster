"""Async admission is distinct from terminal effects and preserves persisted v1 bytes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentRunState,
    ToolExecutionDeferred,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.tool_capability import ToolExecutionResult

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def payload() -> JsonObject:
    return {
        "schema_version": "trace.tool-deferred.v1",
        "invocation_sha256": "a" * 64,
        "operation_id": "capture:operation-1",
        "executor_id": "mac-worker",
    }


def test_deferred_roundtrip_is_nonterminal_and_state_is_explicit() -> None:
    value = ToolExecutionDeferred.model_validate(payload())
    assert value.model_dump(mode="json") == payload()
    assert ToolExecutionDeferred.model_validate_json(value.model_dump_json()) == value
    assert AgentRunState("awaiting_tool") is AgentRunState.AWAITING_TOOL
    with pytest.raises(ValueError, match="validation errors"):
        _ = ToolExecutionResult.model_validate(value.model_dump(mode="json"))


@pytest.mark.parametrize("field", ["output", "actual_cost_units", "disposition", "approved"])
def test_deferred_cannot_claim_terminal_output_cost_or_authority(field: str) -> None:
    value = payload()
    value[field] = "succeeded"
    with pytest.raises(ValueError, match="extra_forbidden"):
        _ = ToolExecutionDeferred.model_validate(value)


@pytest.mark.parametrize("field", ["operation_id", "executor_id"])
def test_deferred_identifiers_have_bounded_valid_shape(field: str) -> None:
    value = payload()
    value[field] = "a" * 160
    _ = ToolExecutionDeferred.model_validate(value)
    for invalid in ("", "a" * 161, "../outside", "worker with spaces"):
        value[field] = invalid
        with pytest.raises(ValueError, match="validation error"):
            _ = ToolExecutionDeferred.model_validate(value)


def test_legacy_terminal_result_and_unscoped_invocation_serialize_unchanged() -> None:
    result: JsonObject = {
        "schema_version": "trace.tool-execution-result.v1",
        "invocation_sha256": "a" * 64,
        "executor_id": "worker",
        "disposition": "succeeded",
        "output": {"artifact": "image"},
        "actual_cost_units": 20,
    }
    parsed = ToolExecutionResult.model_validate(result)
    assert parsed.model_dump(mode="json") == result
    assert contract_sha256(parsed) == contract_sha256(result)
    request: JsonObject = {
        "schema_version": "trace.tool-invocation.v1",
        "invocation_id": "invoke",
        "run_id": "run",
        "step_id": "step",
        "intent_sha256": "b" * 64,
        "capability_snapshot_sha256": "c" * 64,
        "descriptor_sha256": "d" * 64,
        "idempotency_key": "key",
        "input": {},
        "input_sha256": contract_sha256({}),
    }
    invocation = ToolInvocation.model_validate(request)
    assert invocation.model_dump(mode="json") == request
    assert contract_sha256(invocation) == contract_sha256(request)
