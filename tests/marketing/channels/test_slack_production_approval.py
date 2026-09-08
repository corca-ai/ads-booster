# pyright: reportPrivateUsage=false
"""Signed exact natural approval needs delivered complete review and current authority."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.work_continuation import continue_work
from tests.marketing.agent_service.test_application import (
    EffectThenStopReasoning,
    ResearchAdapter,
    _reasoning_result,
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import effect_descriptor, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


@pytest.mark.parametrize("phrase", ["이대로 만들어줘", "이대로 제작해줘"])
def test_signed_reviewed_same_thread_natural_approval(tmp_path: Path, phrase: str) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text=phrase, ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    receive(owner, type="message", text="검토 1", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text=phrase, ts="100.004", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED


def test_review_does_not_preserve_revoked_approval_authority(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    identity = owner.identity("U1")
    with closing(sqlite3.connect(owner.store.database_path)) as db, db:
        _ = db.execute(
            "UPDATE channel_identity_bindings SET binding_json=? WHERE binding_id=?",
            (
                identity.model_copy(update={"can_approve": False}).model_dump_json(),
                identity.binding_id,
            ),
        )
    receive(owner, type="message", text="이대로 만들어줘", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL


def test_frozen_natural_approval_cannot_approve_changed_invocation(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="이대로 만들어줘", ts="100.003", thread_ts="100.001")
    claimed = owner.store.claim()
    assert claimed is not None
    message, _ = claimed
    conversation = owner.store.conversation(message.conversation_id)
    assert conversation is not None
    plan = owner._plan(conversation, message)
    assert plan.action == "approve"
    _ = continue_work(
        service,
        "team",
        conversation.current_run,
        event_id="changed",
        actor_id="member",
        note="Change background",
        action="revise",
        now=NOW,
    )
    with pytest.raises(ValueError, match=r"stale|invocation|approval"):
        _ = owner._execute(conversation, message, plan, owner.identity("U1"), now=NOW)
    assert not any(
        record.kind is AgentRecordKind.RECEIPT
        for record in service.repository.records("team", conversation.current_run)
    )


class ScopedReasoning:
    def __init__(self, capability: str = "capture.appium", *, large: bool = False) -> None:
        self.capability: str = capability
        self.large: bool = large

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        original = EffectThenStopReasoning().plan(request)
        if original.decision.action != "invoke_tool":
            return original
        decision = original.decision.model_copy(
            update={
                "capability_id": self.capability,
                "tool_input": {
                    "screen": "lock-screen",
                    "detail": "x" * 2400 if self.large else "short",
                },
            }
        )
        return _reasoning_result(request, decision)


def test_natural_approval_requires_every_delivered_review_page(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = ScopedReasoning(large=True)
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    pages = owner.commands.review_pages("team", run.run_id)
    assert len(pages) > 1
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="이대로 만들어줘", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    for page in range(2, len(pages) + 1):
        receive(
            owner, type="message", text=f"검토 {page}", ts=f"101.{page:03}", thread_ts="100.001"
        )
        assert owner.work_once(now=NOW)
    receive(owner, type="message", text="이대로 만들어줘", ts="102.001", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED


@pytest.mark.parametrize(
    ("capability", "effect"),
    [
        ("capture.appium", EffectClass.EXTERNAL),
        ("delivery.publish", EffectClass.LOCAL_ARTIFACT),
    ],
)
def test_reviewed_external_or_publication_action_is_not_natural_production_approval(
    tmp_path: Path,
    capability: str,
    effect: EffectClass,
) -> None:
    owner, _ = setup_events(tmp_path)
    service = owner.commands.application.service
    descriptor = effect_descriptor().model_copy(
        update={"capability_id": capability, "effect_class": effect}
    )
    service.registry = ToolRegistry((descriptor,))
    service.reasoning = ScopedReasoning(capability)
    service.tools = {capability: ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="이대로 만들어줘", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL


def test_other_members_review_does_not_count_for_natural_approval(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    original = owner.identity("U1")
    owner.commands.application.store.put_identity(
        original.model_copy(
            update={
                "binding_id": "binding-other",
                "external_user_id": "U2",
                "member_id": "other",
                "can_approve": True,
            }
        )
    )
    owner.commands.allowed_user_ids = frozenset({"U1", "U2"})
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(
        owner, type="message", user="U2", text="이대로 만들어줘", ts="100.003", thread_ts="100.001"
    )
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
