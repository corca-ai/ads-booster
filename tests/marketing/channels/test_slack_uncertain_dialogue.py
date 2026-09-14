from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.waiting_dialogue import answer_waiting_dialogue
from ads_booster.channels.slack_conversations import SlackConversationStore
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentGoal, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from tests.marketing.agent_service.test_application import reasoning_result
from tests.marketing.agent_service.test_application_deferred import (
    AsyncAdapter,
    Reasoning,
    SimulatedCrash,
    build,
    result,
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.channels.slack_conversations import Message
    from ads_booster.channels.task_results import TaskResult
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.transport.json_types import JsonObject


class DialogueReasoning:
    def __init__(self, text: str, answer: str) -> None:
        self.text: str = text
        self.answer: str = answer
        self.requests: list[ReasoningRequest] = []

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        assert self.text in request.model_dump_json()
        return reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                reasoning_summary=self.answer,
                expected_outcome="Answer the current message without executing the pending tool",
            ),
        )


@pytest.fixture
def uncertain_conversation(tmp_path: Path) -> tuple[SlackEvents, list[JsonObject], AsyncAdapter]:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    adapter = AsyncAdapter()
    execution = build(tmp_path, adapter, Reasoning())
    service.registry = ToolRegistry(
        tuple(
            descriptor.model_copy(
                update={"readiness": descriptor.readiness.model_copy(update={"observed_at": NOW})}
            )
            for descriptor in execution.registry.descriptors
        )
    )
    service.tools = execution.tools
    service.reasoning = execution.reasoning
    receive(owner, text="<@UBOT> 캡처해줘")
    assert owner.work_once(now=NOW)
    conversation = owner.store.conversations()[0]
    original_id = conversation.current_run
    invocation = service.pending_approval("team", original_id)
    assert invocation is not None
    _ = service.decide_approval(
        "team",
        original_id,
        approver_id="member",
        granted=True,
        expected_invocation_sha256=contract_sha256(invocation),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    assert len(adapter.calls) == 1
    _ = service.mark_deferred_uncertain(
        "team", original_id, operation_id="op-" + invocation.invocation_id, now=NOW
    )
    for _ in range(4):
        if not owner.work_once(now=NOW):
            break
    return owner, messages, adapter


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("배고파", "뭐 먹고 싶어요?"),
        ("결과 확인할 수 있어?", "현재 저장된 기록에는 완료 결과가 없습니다."),
        ("hey can you check complete?", "The stored operation has no confirmed result yet."),
    ],
)
def test_uncertain_operation_allows_current_dialogue_without_redispatch(
    uncertain_conversation: tuple[SlackEvents, list[JsonObject], AsyncAdapter],
    text: str,
    answer: str,
) -> None:
    owner, messages, adapter = uncertain_conversation
    service = owner.commands.application.service
    original_id = owner.store.conversations()[0].current_run
    original = service.repository.get("team", original_id)
    assert original is not None
    session = service.runtime_store.load(original_id)
    records = service.repository.records("team", original_id)
    reasoning = DialogueReasoning(text, answer)
    service.reasoning = reasoning

    receive(owner, type="message", text=text, ts="100.002", thread_ts="100.001")
    for _ in range(4):
        if not owner.work_once(now=NOW):
            break

    assert len(reasoning.requests) == 1
    assert reasoning.requests[0].capability_snapshot.descriptors == ()
    assert reasoning.requests[0].remaining_tool_calls == 0
    assert messages[-1]["text"] == answer
    assert messages[-1]["thread_ts"] == "100.001"
    assert service.repository.get("team", original_id) == original
    assert original.state is AgentRunState.AWAITING_RECONCILIATION
    assert service.runtime_store.load(original_id) == session
    assert service.repository.records("team", original_id) == records
    assert owner.store.conversations()[0].current_run == original_id
    assert len(adapter.calls) == 1


def test_cached_dialogue_survives_crash_before_delivery(
    uncertain_conversation: tuple[SlackEvents, list[JsonObject], AsyncAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, messages, adapter = uncertain_conversation
    reasoning = DialogueReasoning("배고파", "어떤 음식 좋아해요?")
    owner.commands.application.service.reasoning = reasoning
    receive(owner, type="message", text="배고파", ts="100.002", thread_ts="100.001")

    def crash(
        self: SlackConversationStore,
        message: Message,
        result: str,
        *,
        blocked: bool = False,
        task_result: TaskResult | None = None,
    ) -> None:
        _ = self, message, result, blocked, task_result
        raise SimulatedCrash

    with monkeypatch.context() as patch:
        patch.setattr(SlackConversationStore, "finish", crash)
        with pytest.raises(SimulatedCrash):
            _ = owner.work_once(now=NOW)

    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert len(reasoning.requests) == 1
    assert messages[-1]["text"] == reasoning.answer
    assert len(adapter.calls) == 1


def test_late_original_result_retains_thread_binding_after_dialogue(
    uncertain_conversation: tuple[SlackEvents, list[JsonObject], AsyncAdapter],
) -> None:
    owner, messages, adapter = uncertain_conversation
    service = owner.commands.application.service
    conversation = owner.store.conversations()[0]
    service.reasoning = DialogueReasoning("배고파", "간단히 먹을까요?")
    receive(owner, type="message", text="배고파", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    invocation = adapter.calls[0]

    _ = service.complete_deferred(
        "team",
        conversation.current_run,
        operation_id="op-" + invocation.invocation_id,
        result=result(invocation),
        now=NOW,
    )

    assert owner.store.conversation_for_run("team", conversation.current_run) == conversation
    assert len(adapter.calls) == 1
    session = service.runtime_store.load(conversation.current_run)
    assert session is not None
    assert session.reserved_cost_units == 0
    service.reasoning = Reasoning()
    assert service.drive("team", conversation.current_run, now=NOW).state is AgentRunState.COMPLETED
    assert owner.enqueue_run_update("team", conversation.current_run, event_id="late-worker")
    assert owner.work_once(now=NOW)
    assert messages[-1]["thread_ts"] == conversation.thread_ts
    assert "Bounded asynchronous capture" in str(messages[-1]["text"])
    count = len(messages)
    assert not owner.enqueue_run_update("team", conversation.current_run, event_id="late-worker")
    assert len(messages) == count


def test_delivered_dialogue_is_not_repeated_after_restart(
    uncertain_conversation: tuple[SlackEvents, list[JsonObject], AsyncAdapter],
) -> None:
    owner, messages, adapter = uncertain_conversation
    reasoning = DialogueReasoning("배고파", "점심 먹었어요?")
    owner.commands.application.service.reasoning = reasoning
    receive(owner, type="message", text="배고파", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    count = len(messages)
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()

    receive(restarted, type="message", text="배고파", ts="100.002", thread_ts="100.001")
    assert not restarted.work_once(now=NOW)

    assert len(reasoning.requests) == 1
    assert len(messages) == count
    assert len(adapter.calls) == 1


class ToolRequestReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        return reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="capture.test",
                tool_input={"index": 0},
                reasoning_summary="Retry the pending capture",
                expected_outcome="Capture",
            ),
        )


def test_dialogue_rejects_tool_dispatch_and_preserves_pending_operation(
    uncertain_conversation: tuple[SlackEvents, list[JsonObject], AsyncAdapter],
) -> None:
    owner, _, adapter = uncertain_conversation
    service = owner.commands.application.service
    run_id = owner.store.conversations()[0].current_run
    run = service.repository.get("team", run_id)
    assert run is not None
    before = service.runtime_store.load(run_id)
    service.reasoning = ToolRequestReasoning()

    with pytest.raises(ValueError, match="waiting_dialogue_tool_execution_forbidden"):
        _ = answer_waiting_dialogue(
            service, run, AgentGoal(objective="다시 실행해줘", success_criteria=("Respond",))
        )

    assert service.repository.get("team", run_id) == run
    assert service.runtime_store.load(run_id) == before
    assert len(adapter.calls) == 1
