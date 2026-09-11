# pyright: reportPrivateUsage=false
"""A conversational answer must not complete an unexecuted pending action."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.contracts.tool_capability import EffectClass
from tests.marketing.agent_service.test_application import (
    EffectThenStopReasoning,
    ResearchAdapter,
    _descriptor,
    _reasoning_result,
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import effect_descriptor, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


class ReadThenAnswer:
    def __init__(self) -> None:
        self.calls: int = 0

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.calls += 1
        assert request.pending_approval is not None
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool" if self.calls == 1 else "stop",
                capability_id="research.web" if self.calls == 1 else None,
                tool_input={"query": "pastel style"} if self.calls == 1 else None,
                expected_outcome="Answer style question without executing the pending image",
                reasoning_summary="파스텔 톤으로 준비되어 있어요. 제작은 아직 시작하지 않았어요.",
            ),
        )


@pytest.mark.parametrize(
    "interrupt_point", ["", "approval_committed", "execute_committed", "runtime_admitted"]
)
def test_read_tool_does_not_replace_pending_action_or_approved_recovery_target(
    tmp_path: Path,
    interrupt_point: str,
) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    research_descriptor = _descriptor("research.web", EffectClass.OBSERVE, ready=True)
    research_descriptor = research_descriptor.model_copy(
        update={
            "readiness": research_descriptor.readiness.model_copy(update={"observed_at": NOW}),
        }
    )
    service.registry = ToolRegistry(
        (
            effect_descriptor(),
            research_descriptor,
        )
    )
    image, research = ResearchAdapter(), ResearchAdapter()
    service.tools = {"creative.image.edit": image, "research.web": research}
    service.reasoning = EffectThenStopReasoning()
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    pending = service.pending_approval("team", run.run_id)
    assert pending is not None
    service.reasoning = ReadThenAnswer()
    receive(
        owner,
        type="message",
        text="이 스타일 특징을 찾아 설명해줘",
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert len(research.inputs) == 1
    assert not image.inputs
    assert service.pending_approval("team", run.run_id) == pending
    assert "lock-screen" in owner.commands.review("team", run.run_id, 1)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    service.reasoning = EffectThenStopReasoning()

    def crash(point: str) -> None:
        if point == interrupt_point:
            message = "test interruption"
            raise RuntimeError(message)

    if interrupt_point:
        service.fault_hook = crash
    receive(
        owner,
        type="message",
        text=f"승인 {contract_sha256(pending)}",
        ts="100.003",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    service.fault_hook = None
    _ = service.drive("team", run.run_id, now=NOW)
    assert image.inputs == [pending.input]
    assert len(research.inputs) == 1
    assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED


class CancelPending:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        result = EffectThenStopReasoning().plan(request)
        return _reasoning_result(
            request,
            result.decision.model_copy(
                update={
                    "pending_approval_action": "cancel",
                    "reasoning_summary": "제작 요청을 취소했어요.",
                }
            ),
        )


def test_explicit_cancellation_prevents_old_hash_from_executing(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    image = ResearchAdapter()
    service.tools = {"creative.image.edit": image}
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    pending = service.pending_approval("team", run.run_id)
    assert pending is not None
    service.reasoning = CancelPending()
    receive(owner, type="message", text="이미지 만들지 마", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(
        owner,
        type="message",
        text=f"승인 {contract_sha256(pending)}",
        ts="100.003",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert service.pending_approval("team", run.run_id) is None
    assert not image.inputs


def test_read_acknowledgement_preserves_exact_approval_after_restart(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    adapter = ResearchAdapter()
    service.tools = {"creative.image.edit": adapter}
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    invocation = next(
        r
        for r in reversed(service.repository.records("team", run.run_id))
        if r.kind is AgentRecordKind.INVOCATION
    )
    digest = contract_sha256(invocation.payload)
    receive(owner, type="message", text="읽었어", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    assert not adapter.inputs
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    receive(restarted, type="message", text="상태", ts="100.003", thread_ts="100.001")
    assert restarted.work_once(now=NOW)
    assert "completed" not in str(messages[-1]["text"])
    receive(restarted, type="message", text=f"승인 {digest}", ts="100.004", thread_ts="100.001")
    assert restarted.work_once(now=NOW)
    assert len(adapter.inputs) == 1
    assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED


def test_queued_assent_cannot_approve_a_proposal_delivered_later(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    adapter = ResearchAdapter()
    service.tools = {"creative.image.edit": adapter}
    receive(owner)
    receive(owner, type="message", text="승인", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    # Identical redelivery after the proposal appears must preserve the original empty binding.
    receive(owner, type="message", text="승인", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert not adapter.inputs


@pytest.mark.parametrize("mutation", ["message_changed", "message_deleted"])
def test_frozen_assent_rechecks_source_after_edit_or_deletion(
    tmp_path: Path, mutation: str
) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    adapter = ResearchAdapter()
    service.tools = {"creative.image.edit": adapter}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="승인", ts="100.002", thread_ts="100.001")
    claimed = owner.store.claim()
    assert claimed is not None
    message, _ = claimed
    conversation = owner.store.conversation(message.conversation_id)
    assert conversation is not None
    plan = owner._plan(conversation, message)
    assert plan.action == "approve"
    receive(
        owner,
        subtype=mutation,
        user="U1",
        event_ts="100.003",
        deleted_ts="100.002",
        message={
            "type": "message",
            "user": "U1",
            "text": "취소해",
            "ts": "100.002",
            "edited": {"user": "U1", "ts": "100.003"},
        },
        previous_message={"type": "message", "user": "U1", "text": "승인", "ts": "100.002"},
    )
    with pytest.raises(ValueError, match="slack_approval_source_changed"):
        _ = owner._execute(conversation, message, plan, owner.identity("U1"), now=NOW)
    assert not adapter.inputs


def test_sentence_starting_with_approval_is_not_a_hash_command(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 승인 대기 중에 작업이 끝나는 버그를 설명해줘")
    assert owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1
