"""Signed Slack steering is retained while a fake asynchronous tool is pending."""

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
from ads_booster.marketing.agent_core.registry import ToolRegistry
from tests.marketing.agent_service.test_application import EffectThenStopReasoning
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import effect_descriptor, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tool_capability import ToolDescriptor


class DeferredCapture:
    def __init__(self) -> None:
        self.calls: int = 0
        self.invocation: ToolInvocation | None = None

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionDeferred:
        assert descriptor.capability_id == "creative.image.edit"
        self.calls += 1
        self.invocation = invocation
        return ToolExecutionDeferred(
            schema_version="trace.tool-deferred.v1",
            invocation_sha256=contract_sha256(invocation),
            operation_id="fixture-operation",
            executor_id="fixture-worker",
        )


@pytest.mark.parametrize("note", ["잠깐 멈춰줘", "학생 타깃으로 바꿔줘"])
def test_signed_followup_keeps_pending_work_and_applies_after_completion(
    tmp_path: Path, note: str
) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    adapter = DeferredCapture()
    service.registry = ToolRegistry((effect_descriptor(),))
    service.tools = {"creative.image.edit": adapter}
    service.reasoning = EffectThenStopReasoning()
    receive(owner, text="<@UBOT> 배경을 캡처해줘")
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    invocation = service._latest_invocation("team", run.run_id)  # pyright: ignore[reportPrivateUsage]
    receive(
        owner,
        type="message",
        text="승인 " + contract_sha256(invocation),
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_TOOL
    receive(owner, type="message", text=note, ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    waiting = service.repository.list_runs("team")[0]
    assert waiting.state is AgentRunState.AWAITING_TOOL
    assert note in str(
        [record.payload for record in service.repository.records("team", run.run_id)]
    )
    assert "이미 시작된 작업이 취소됐다는 뜻은 아닙니다" in str(messages)
    assert adapter.calls == 1
    result = ToolExecutionResult(
        schema_version="trace.tool-execution-result.v1",
        invocation_sha256=contract_sha256(invocation),
        executor_id="fixture-worker",
        disposition="succeeded",
        output={"fixture": "done"},
        actual_cost_units=1,
    )
    completed = service.complete_deferred(
        "team", run.run_id, operation_id="fixture-operation", result=result, now=NOW
    )
    expected = AgentRunState.AWAITING_INPUT if note == "잠깐 멈춰줘" else AgentRunState.COMPLETED
    assert completed.state is expected
    assert adapter.calls == 1
