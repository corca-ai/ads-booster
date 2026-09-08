"""Installed creative guidance uses the current host catalog and trusted Run budget."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def test_creative_prepare_observes_late_registry_and_host_policy(tmp_path: Path) -> None:
    service = build_installed_marketing_agent_service(
        paths=InstalledServicePaths(tmp_path),
        codex_executable=Path("/unused/codex"),
        model_id="test-model",
        timeout_seconds=1,
    )
    now = datetime.now(UTC)
    run = AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="run-a",
        tenant_id="tenant-a",
        goal=AgentGoal(objective="Review background", success_criteria=("Readable",)),
        budget=AgentBudget(max_tool_calls=5, max_cost_units=5),
        created_at=now,
        updated_at=now,
    )
    _ = service.repository.create(run)
    descriptor = next(
        item for item in service.registry.descriptors if item.capability_id == "creative.prepare"
    )
    image = descriptor.model_copy(update={"capability_id": "creative.image.review"})
    service.registry = ToolRegistry((descriptor, image))
    payload: JsonObject = {"task": "background_review", "inputs": {"asset_ids": ["asset-a"]}}
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        invocation_id="invoke-a",
        step_id="step-a",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="key-a",
        input=payload,
        input_sha256=contract_sha256(payload),
    )
    adapter = service.tools["creative.prepare"]
    assert adapter.execute(invocation, descriptor).output["route"] == "automatic"
    service.capability_policy = CapabilityPolicy(denied_capability_ids=("creative.image.review",))
    assert adapter.execute(invocation, descriptor).output["route"] == "human_assisted"
    service.capability_policy = CapabilityPolicy()
    service.registry = ToolRegistry(
        (
            descriptor,
            image.model_copy(
                update={"readiness": image.readiness.model_copy(update={"ready": False})}
            ),
        )
    )
    assert adapter.execute(invocation, descriptor).output["route"] == "human_assisted"
    with pytest.raises(ValueError, match="creative_run_context_required"):
        _ = adapter.execute(invocation.model_copy(update={"tenant_id": "tenant-b"}), descriptor)

    service.registry = ToolRegistry(
        (
            descriptor,
            image.model_copy(
                update={"cost": image.cost.model_copy(update={"worst_case_units": 6})}
            ),
        )
    )
    assert adapter.execute(invocation, descriptor).output["route"] == "human_assisted"
    service.registry = ToolRegistry((descriptor, image))
    exhausted = run.model_copy(
        update={
            "run_id": "run-exhausted",
            "budget": AgentBudget(max_tool_calls=0, max_cost_units=5),
        }
    )
    _ = service.repository.create(exhausted)
    assert (
        adapter.execute(
            invocation.model_copy(update={"run_id": exhausted.run_id}), descriptor
        ).output["route"]
        == "human_assisted"
    )
