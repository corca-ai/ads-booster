"""Human performance reports are scoped inputs, never live measurements."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.marketing.agent_service.performance_observations import PerformanceObservationStore
from ads_booster.marketing.channels.slack_conversations import Conversation, Message
from ads_booster.marketing.channels.slack_performance import (
    is_performance_command,
    performance_command,
)
from ads_booster.transport.json_types import JsonObject
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.channels.slack_events import SlackEvents


def payload() -> str:
    return json.dumps(
        {
            "channel": "threads",
            "account_id": "trace-jp",
            "country": "JP",
            "publication_ref": "https://threads.net/@fixture/post/one",
            "window_start": "2026-09-06T00:00:00Z",
            "window_end": "2026-09-07T00:00:00Z",
            "views": 100,
            "likes": 10,
            "comments": 2,
        }
    )


def test_only_explicit_performance_commands_are_intercepted() -> None:
    assert is_performance_command("성과 기록 " + payload())
    assert is_performance_command("성과 비교 one two")
    assert not is_performance_command("성과를 보고 다음 실험을 제안해줘")


def test_record_replay_and_untrusted_scope_are_bounded(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    service = events.commands.application.service
    run = service.repository.list_runs("team")[0]
    conversation = events.store.conversation_for_run("team", run.run_id)
    assert conversation is not None
    identity = events.identity("U1")
    message = Message(
        message_id="metric-one",
        conversation_id=conversation.conversation_id,
        user_id="U1",
        text="성과 기록 " + payload(),
    )
    reply = performance_command(service, conversation, message, identity, now=NOW)
    assert "사람 보고" in reply
    assert "performance-observation-" in reply
    assert performance_command(service, conversation, message, identity, now=NOW) == reply
    forged = message.model_copy(
        update={"message_id": "forged", "text": '성과 기록 {"scope":{"workspace_id":"other"}}'}
    )
    assert "필수" in performance_command(service, conversation, forged, identity, now=NOW)
    other = identity.model_copy(update={"tenant_id": "other"})
    assert "범위" in performance_command(service, conversation, message, other, now=NOW)


def test_signed_same_thread_record_correction_compare_learning(tmp_path: Path) -> None:
    events, messages = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    service = events.commands.application.service
    run = service.repository.list_runs("team")[0]
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="team", product_id="trace", work_id=run.run_id),
        actor_id="member",
    )
    store = PerformanceObservationStore(service.repository.database_path)
    receive(
        events, type="message", text="성과 기록 " + payload(), ts="100.002", thread_ts="100.001"
    )
    assert events.work_once(now=NOW)
    first = store.list(access)[0]
    alternate: JsonObject = TypeAdapter[JsonObject](JsonObject).validate_json(payload())
    alternate.update({"country": "US", "account_id": "trace-us", "views": 200})
    receive(
        events,
        type="message",
        text="성과 기록 " + json.dumps(alternate),
        ts="100.003",
        thread_ts="100.001",
    )
    assert events.work_once(now=NOW)
    second = next(
        item for item in store.list(access) if item.observation_id != first.observation_id
    )
    receive(
        events,
        type="message",
        text=f"성과 비교 {first.observation_id} {second.observation_id}",
        ts="100.004",
        thread_ts="100.001",
    )
    assert events.work_once(now=NOW)
    assert "조건이 다릅니다" in str(messages[-1])
    assert "미보고" in str(messages[-1])
    receive(
        events,
        type="message",
        text=f"성과 학습 {first.observation_id} 관심 증가 | 계정 차이 | 일본",
        ts="100.005",
        thread_ts="100.001",
    )
    assert events.work_once(now=NOW)
    assert "기억 검토" in str(messages[-1])
    revised: JsonObject = TypeAdapter[JsonObject](JsonObject).validate_json(payload())
    revised["views"] = 90
    receive(
        events,
        type="message",
        text=f"성과 정정 {first.observation_id} " + json.dumps(revised),
        ts="100.006",
        thread_ts="100.001",
    )
    assert events.work_once(now=NOW)
    assert sorted(item.views for item in store.list(access)) == [90, 200]
    assert len(service.repository.list_runs("team")) == 1


def test_private_report_cannot_be_read_from_shared_scope(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    receive(events, type="message", channel="D1", channel_type="im", text="성과를 정리할게")
    assert events.work_once(now=NOW)
    conversation = next(
        Conversation.model_validate_json(row[0])
        for row in _conversations(events)
        if Conversation.model_validate_json(row[0]).private
    )
    message = Message(
        message_id="private-metric",
        conversation_id=conversation.conversation_id,
        user_id="U1",
        text="성과 기록 " + payload(),
    )
    reply = performance_command(
        events.private_service, conversation, message, events.identity("U1"), now=NOW
    )
    assert "사람 보고" in reply
    shared = MemoryAccess(
        scope=MemoryScope(
            workspace_id="team", product_id="trace", work_id=conversation.current_run
        ),
        actor_id="member",
    )
    assert PerformanceObservationStore(events.store.database_path).list(shared) == ()


def _conversations(events: SlackEvents) -> list[tuple[str]]:
    with events.store.connect() as db:
        return TypeAdapter(list[tuple[str]]).validate_python(
            db.execute("SELECT data_json FROM slack_conversations").fetchall()
        )


def test_correction_requires_original_author_or_reviewer(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    service = events.commands.application.service
    run = service.repository.list_runs("team")[0]
    conversation = events.store.conversation_for_run("team", run.run_id)
    assert conversation is not None
    identity = events.identity("U1")
    message = Message(
        message_id="original",
        conversation_id=conversation.conversation_id,
        user_id="U1",
        text="성과 기록 " + payload(),
    )
    _ = performance_command(service, conversation, message, identity, now=NOW)
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="team", product_id="trace", work_id=run.run_id),
        actor_id="member",
    )
    store = PerformanceObservationStore(service.repository.database_path)
    first = store.list(access)[0]
    correction = message.model_copy(
        update={
            "message_id": "correction",
            "text": f"성과 정정 {first.observation_id} " + payload(),
        }
    )
    peer = identity.model_copy(update={"member_id": "other-member", "can_approve": False})
    assert "정정 권한" in performance_command(service, conversation, correction, peer, now=NOW)
    assert store.list(access) == (first,)
    reviewer = peer.model_copy(update={"can_approve": True})
    assert "사람 보고" in performance_command(service, conversation, correction, reviewer, now=NOW)
    assert store.list(access)[0].author_id == "other-member"
