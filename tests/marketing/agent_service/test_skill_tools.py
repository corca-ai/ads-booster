from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.marketing.agent_service.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_integrations import (
    UnusedResearchRunner,
    _invocation,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


class DiscoverThenRead(AskThenStopReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        outputs = [e["output"] for e in request.evidence if "output" in e]
        if not outputs:
            capability, payload = "skills.list", {}
        elif len(outputs) == 1:
            index = outputs[0]
            assert isinstance(index, dict)
            skills = index["skills"]
            assert isinstance(skills, list)
            choice = next(
                s for s in skills if isinstance(s, dict) and s["skill_id"] == "marketing.copy"
            )
            assert isinstance(choice, dict)
            assert "procedure" not in choice
            capability, payload = (
                "skills.read",
                {"skill_id": choice["skill_id"], "version": choice["version"]},
            )
        else:
            procedure = outputs[-1]
            assert isinstance(procedure, dict)
            assert procedure["status"] == "found"
            assert procedure["procedure"]
            assert procedure["authority"] == "procedure_only_not_evidence_or_approval"
            return _reasoning_result(
                request,
                ReasoningDecision(
                    schema_version="trace.reasoning-decision.v1",
                    action="stop",
                    expected_outcome="Return the copy",
                    reasoning_summary="내일 할 일, 오늘 가볍게 정리해요.",
                ),
            )
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id=capability,
                tool_input=payload,
                expected_outcome="Load only the relevant procedure",
                reasoning_summary="Discover reusable guidance",
            ),
        )


def test_discovery_loads_versioned_procedure_through_real_run_receipts(tmp_path: Path) -> None:
    configured = ConfiguredAgentTools(AgentServiceIntegrationConfig(), UnusedResearchRunner())
    reasoning = DiscoverThenRead()
    service = _service(tmp_path / "agent.db", reasoning)
    service.registry = ToolRegistry(configured.descriptors(now=NOW))
    service.tools = configured.adapters()
    service.capability_policy = CapabilityPolicy(
        allowed_capability_ids=("skills.list", "skills.read")
    )
    completed = service.create(_request(), now=NOW)
    assert completed.state is AgentRunState.COMPLETED
    assert len(reasoning.requests) == 3
    receipts = [
        r
        for r in service.repository.records("trace", completed.run_id)
        if r.kind is AgentRecordKind.RECEIPT
    ]
    assert len(receipts) == 2
    assert all(r.payload["actual_cost_units"] == 0 for r in receipts)
    assert all(
        d.capability_id in {"skills.list", "skills.read"}
        for request in reasoning.requests
        for d in request.capability_snapshot.descriptors
    )


@pytest.mark.parametrize(
    ("skill_id", "version"), [("../../secret", "1"), ("marketing.copy", "999")]
)
def test_unknown_skill_or_version_is_recoverable_without_loading_external_content(
    skill_id: str,
    version: str,
) -> None:
    configured = ConfiguredAgentTools(AgentServiceIntegrationConfig(), UnusedResearchRunner())
    descriptor = next(
        d for d in configured.descriptors(now=NOW) if d.capability_id == "skills.read"
    )
    result = configured.adapters()["skills.read"].execute(
        _invocation(descriptor, {"skill_id": skill_id, "version": version}),
        descriptor,
    )
    assert isinstance(result, ToolExecutionResult)
    assert result.output["status"] == "not_found"
    assert "procedure" not in result.output
    assert result.actual_cost_units == 0
