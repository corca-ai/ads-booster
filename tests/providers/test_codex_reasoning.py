from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentGoal,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.reasoning import ReasoningRequest
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)
from ads_booster.providers.codex_reasoning import CodexReasoningError, CodexReasoningProvider

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class StructuredRunner:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        self.prompts.append(prompt)
        assert schema["type"] == "object"
        assert workspace.is_dir()
        assert timeout_seconds == 30
        return {
            "schema_version": "trace.reasoning-decision.v1",
            "action": "stop",
            "capability_id": None,
            "tool_input": None,
            "expected_outcome": "The bounded planning question is answered",
            "reasoning_summary": "No further tool is justified",
        }


class FailingStructuredRunner:
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = prompt, schema, workspace, timeout_seconds
        message = "provider detail must not cross the adapter"
        raise RuntimeError(message)


def test_codex_cli_is_only_one_reasoning_provider_adapter(tmp_path: Path) -> None:
    runner = StructuredRunner()
    provider = CodexReasoningProvider(
        runner,
        tmp_path,
        model_id="gpt-test",
        timeout_seconds=30,
    )
    request = _request()

    result = provider.plan(request)

    assert result.receipt.provider_id == "official-codex-cli"
    assert result.receipt.request_sha256 == contract_sha256(request)
    assert result.decision.action == "stop"
    assert "capture.appium" not in runner.prompts[0]


def test_codex_runtime_failure_is_sanitized_at_provider_boundary(tmp_path: Path) -> None:
    provider = CodexReasoningProvider(
        FailingStructuredRunner(),
        tmp_path,
        model_id="gpt-test",
        timeout_seconds=30,
    )

    with pytest.raises(CodexReasoningError, match="reasoning_provider_result_invalid") as caught:
        _ = provider.plan(_request())

    assert "provider detail" not in str(caught.value)


def _request() -> ReasoningRequest:
    schema: JsonObject = {"type": "object"}
    schema_sha256 = contract_sha256(schema)
    descriptor = ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id="research.web",
        version="1",
        owner="research.adapter",
        installation_id="research-installation",
        input_schema=schema,
        input_schema_sha256=schema_sha256,
        output_schema=schema,
        output_schema_sha256=schema_sha256,
        config_schema=schema,
        config_schema_sha256=schema_sha256,
        receipt_schema=schema,
        receipt_schema_sha256=schema_sha256,
        credential_boundary="none",
        effect_class=EffectClass.OBSERVE,
        approval_policy=ToolApprovalPolicy(mode="none"),
        cost=ToolCost(worst_case_units=1, unit="operation"),
        readiness=ToolReadiness(ready=True, observed_at=NOW, max_age_seconds=300),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode="none",
            terminal_dispositions=("succeeded", "failed"),
        ),
    )
    return ReasoningRequest(
        schema_version="trace.reasoning-request.v1",
        run_id="run-one",
        phase="plan",
        goal=AgentGoal(objective="Choose a format", success_criteria=("one experiment",)),
        capability_snapshot=CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id="snapshot-one",
            run_id="run-one",
            descriptors=(descriptor,),
            created_at=NOW,
        ),
        remaining_tool_calls=2,
        remaining_cost_units=4,
    )
