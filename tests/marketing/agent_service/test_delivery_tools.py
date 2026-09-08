"""A model prepares a real durable review packet without an external action or grant."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.delivery_api import PrepareDelivery
from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore
from ads_booster.marketing.agent_service.delivery_tools import (
    DeliveryPreparationTool,
    DeliveryPrepareRequest,
    delivery_prepare_descriptor,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.marketing.tool_adapters.compatibility import DelegatingToolAdapter

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def payload() -> JsonObject:
    return {
        "proposal_id": "japan-production",
        "rationale": "학생 대상 두 후보 중 달력 가독성이 좋은 여백 중심 구성을 추천",
        "target": {
            "kind": "production",
            "input_sha256": "a" * 64,
            "instructions": "일본 대학생 대상 홍보용 배경 2개 제작안",
            "preserve": ["캐릭터와 실제 달력"],
            "change": ["위쪽 여백"],
            "max_cost_units": 0,
        },
    }


class PrepareAndWait:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="request_input" if request.evidence else "invoke_tool",
            capability_id=None if request.evidence else "delivery.prepare",
            tool_input=None if request.evidence else payload(),
            reasoning_summary="제작 준비안을 별도 검토하고 게시 승인과 분리",
            expected_outcome="실행안 검토 japan-production",
        )
        if request.evidence:
            assert "external_execution_enabled" in str(request.evidence)
            assert "japan-production" in str(request.evidence)
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake.reasoning",
                model_id="fake-model",
                request_sha256=contract_sha256(request),
                output_schema_sha256="d" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def test_reasoning_prepares_durable_same_run_review_without_grant(tmp_path: Path) -> None:
    database = tmp_path / "service.db"
    repository = SqliteAgentRunRepository(database)
    store = DeliveryReviewStore(database)
    tool = DeliveryPreparationTool(store, repository=repository)
    descriptor = delivery_prepare_descriptor(now=NOW)
    service = MarketingAgentService(
        repository=repository,
        registry=ToolRegistry((descriptor,)),
        reasoning=PrepareAndWait(),
        tools={
            "delivery.prepare": DelegatingToolAdapter(
                capability_id="delivery.prepare",
                version="1",
                executor_id="local.delivery",
                executor=tool.execute,
            )
        },
        runtime_store=SqliteSessionStore(database),
    )
    run = service.create(
        CreateAgentRunRequest(
            run_id="japan-run",
            tenant_id="team",
            goal=AgentGoal(
                objective="일본 Threads에서 새 기능을 알려줘", success_criteria=("제작안 검토",)
            ),
            budget=AgentBudget(max_tool_calls=1, max_cost_units=0),
        ),
        now=NOW,
    )
    assert run.state is AgentRunState.AWAITING_INPUT
    scope = CreativeScope(workspace_id="team", product_id="trace")
    packet = DeliveryReviewStore(database).get(scope, "japan-production")
    assert packet is not None
    assert packet.proposal.run_id == run.run_id
    assert packet.state == "draft"
    assert packet.approved_target_sha256 is None
    assert packet.external_execution_enabled is False
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="prepare-replay",
        tenant_id="team",
        run_id=run.run_id,
        step_id="step",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="same-preparation",
        input=payload(),
        input_sha256=contract_sha256(payload()),
    )
    replay = tool.execute(invocation, descriptor)
    assert replay.output["revision"] == 1
    for tenant in (None, "other"):
        with pytest.raises(ValueError, match=r"delivery_(tenant_context_required|run_not_found)"):
            _ = tool.execute(invocation.model_copy(update={"tenant_id": tenant}), descriptor)
    with pytest.raises(ValueError, match="delivery_run_not_found"):
        _ = tool.execute(invocation.model_copy(update={"run_id": "missing"}), descriptor)
    assert (
        store.get(CreativeScope(workspace_id="other", product_id="trace"), "japan-production")
        is None
    )
    with pytest.raises(ValueError, match="delivery_execution_review_required"):
        _ = store.schedule(scope, "japan-production", expected_revision=1, now=NOW)


@pytest.mark.parametrize(
    "key",
    ["scope", "tenant_id", "run_id", "approval", "external_execution_enabled", "d1_campaign_id"],
)
@pytest.mark.parametrize("request_model", [DeliveryPrepareRequest, PrepareDelivery])
def test_request_rejects_authority_and_retired_fields(
    key: str, request_model: type[DeliveryPrepareRequest | PrepareDelivery]
) -> None:
    body = payload()
    body[key] = "forged"
    with pytest.raises(ValueError, match="extra_forbidden"):
        _ = request_model.model_validate(body)
