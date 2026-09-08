from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.agent_service.knowledge_ingress_schema import (
    KnowledgeIngressConflictError,
)
from ads_booster.marketing.channels.slack_events import SlackEvents
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path


def test_completed_same_thread_followup_keeps_knowledge_run_binding(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    receive(owner, type="message", text="둘째 질문", ts="100.002", thread_ts="100.001")
    latest = owner.store.latest_knowledge_event(owner._message_id("C1", "100.002"))  # pyright: ignore[reportPrivateUsage]
    assert latest is not None
    assert latest[1].run_id == run.run_id
    assert latest[1].action == "input"
    assert owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1


def test_queued_followup_execution_binding_survives_restart_without_rewriting_ingress(
    tmp_path: Path,
) -> None:

    owner, _ = setup_events(tmp_path)
    receive(owner)
    receive(owner, type="message", text="둘째 질문", ts="100.002", thread_ts="100.001")
    message_id = owner._message_id("C1", "100.002")  # pyright: ignore[reportPrivateUsage]
    admitted = owner.store.latest_knowledge_event(message_id)
    assert admitted is not None
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert restarted.store.knowledge_ingress.execution_run_for_message(message_id) == run.run_id
    assert restarted.store.latest_knowledge_event(message_id) == admitted
    binding = restarted.store.knowledge_ingress.binding_for_run(run.run_id)
    assert binding is not None
    assert binding.binding_id == admitted[1].binding_id
    restarted.store.knowledge_ingress.bind_execution(message_id, run.run_id, actor_id="member")
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1


def test_mutation_of_rebound_message_fences_actual_run(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    receive(owner)
    receive(owner, type="message", text="둘째 질문", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    receive(
        owner,
        type="message",
        subtype="message_changed",
        ts="100.003",
        event_ts="100.003",
        message={"user": "U1", "text": "정정한 질문", "ts": "100.002", "edited": {"ts": "100.003"}},
        previous_message={"user": "U1", "text": "둘째 질문", "ts": "100.002"},
    )
    assert owner.store.knowledge_ingress.pending_fence_for_run(run.run_id)


def test_execution_binding_rejects_actor_change_and_reassignment(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    receive(owner, type="message", channel="D1", channel_type="im", text="개인 질문")
    assert owner.work_once(now=NOW)
    message_id = owner._message_id("D1", "100.001")  # pyright: ignore[reportPrivateUsage]
    ingress = owner.store.knowledge_ingress
    run_id = ingress.execution_run_for_message(message_id)
    assert run_id is not None
    binding = ingress.binding_for_run(run_id)
    assert binding is not None
    assert binding.actor.conversation_scope.member_id == "member"
    assert binding.actor.conversation_scope.session_id
    with pytest.raises(KnowledgeIngressConflictError, match="actor_mismatch"):
        ingress.bind_execution(message_id, run_id, actor_id="other-member")
    with pytest.raises(KnowledgeIngressConflictError, match="run_mismatch"):
        ingress.bind_execution(message_id, "different-run", actor_id="member")
    assert ingress.binding_for_run(run_id) == binding
