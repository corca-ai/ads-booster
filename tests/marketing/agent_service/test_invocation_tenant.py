"""Tenant authority must bind new tool calls without invalidating frozen v1 approvals."""

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def test_legacy_invocation_digest_and_explicit_tenant_binding() -> None:
    legacy: JsonObject = {
        "schema_version": "trace.tool-invocation.v1",
        "invocation_id": "invocation",
        "run_id": "run",
        "step_id": "step",
        "intent_sha256": "a" * 64,
        "capability_snapshot_sha256": "b" * 64,
        "descriptor_sha256": "c" * 64,
        "idempotency_key": "key",
        "input": {},
        "input_sha256": contract_sha256({}),
    }
    previous = ToolInvocation.model_validate(legacy)
    assert contract_sha256(previous) == contract_sha256(legacy)
    scoped = previous.model_copy(update={"tenant_id": "team"})
    assert contract_sha256(scoped) != contract_sha256(previous)
    assert ToolInvocation.model_validate_json(scoped.model_dump_json()).tenant_id == "team"
