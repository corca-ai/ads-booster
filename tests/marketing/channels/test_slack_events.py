from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, override

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import AgentRunState, ToolInvocation, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from ads_booster.marketing.agent_service.maintenance import MaintenanceGate
from ads_booster.marketing.channels.slack import slack_signature
from ads_booster.marketing.channels.slack_events import SlackEvents
from tests.marketing.agent_service.test_application import (
    AskThenStopReasoning,
    EffectThenStopReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_http_api import StopReasoning
from tests.marketing.channels.test_slack_commands import NOW, setup_commands

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.contracts.tool_capability import ToolDescriptor

from ads_booster.transport.json_types import JsonObject

_JSON_MESSAGES: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])
_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


class RecordingReasoning(StopReasoning):
    def __init__(self) -> None:
        self.requests: list[ReasoningRequest] = []

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        return super().plan(request)


def effect_descriptor() -> ToolDescriptor:
    descriptor = _descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True)
    return descriptor.model_copy(
        update={"readiness": descriptor.readiness.model_copy(update={"observed_at": NOW})}
    )


def setup_events(root: Path) -> tuple[SlackEvents, list[JsonObject]]:
    commands = setup_commands(root)
    messages: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        messages.append(payload)
        return {"ok": True, "ts": "123.456"}

    commands.sender = send
    return SlackEvents(commands, "UBOT", frozenset({"C1"})), messages


def signed(envelope: JsonObject) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(envelope).encode()
    timestamp = str(int(NOW.timestamp()))
    return body, {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": slack_signature(b"test-secret", body, timestamp),
    }


def event(**fields: object) -> tuple[bytes, dict[str, str]]:
    # JSON roundtrip validates the transport, not Python's untyped keyword values.
    adapter: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
    return signed(
        adapter.validate_json(
            json.dumps(
                {
                    "type": "event_callback",
                    "api_app_id": "A1",
                    "team_id": "T1",
                    "event_id": "Ev1",
                    "event": {
                        "type": "app_mention",
                        "channel": "C1",
                        "user": "U1",
                        "text": "<@UBOT> 첫 질문",
                        "ts": "100.001",
                        **fields,
                    },
                }
            )
        )
    )


def receive(owner: SlackEvents, **fields: object) -> None:
    body, headers = event(**fields)
    assert owner.receive(body, headers, now=NOW) == {"ok": True}


def test_ack_dedupe_restart_and_thread_followup_preserve_context(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    reasoning = RecordingReasoning()
    owner.commands.application.service.reasoning = reasoning
    receive(owner)
    receive(owner, type="message")  # Slack emits both types for a mention.
    receive(owner)
    assert not reasoning.requests
    assert not messages
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert len(reasoning.requests) == 1
    assert len(messages) == 2  # accepted + answer, both in the original thread
    assert all(m["thread_ts"] == "100.001" and m["channel"] == "C1" for m in messages)
    assert not restarted.work_once(now=NOW)
    receive(restarted, type="message", text="둘째 질문", ts="100.002", thread_ts="100.001")
    assert restarted.work_once(now=NOW)
    assert len(reasoning.requests) == 2
    assert "첫 질문" in reasoning.requests[-1].model_dump_json()
    assert "둘째 질문" in reasoning.requests[-1].model_dump_json()
    runs = owner.commands.application.service.repository.list_runs("team")
    assert len(runs) == 1
    assert reasoning.requests[0].run_id == reasoning.requests[1].run_id


def test_reply_to_input_uses_same_canonical_run(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    owner.commands.application.service.reasoning = AskThenStopReasoning()
    receive(owner)
    assert owner.work_once(now=NOW)
    repository = owner.commands.application.service.repository
    before = repository.list_runs("team")[0]
    assert before.state is AgentRunState.AWAITING_INPUT
    receive(owner, type="message", text="목표 고객은 대학생", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    after = repository.list_runs("team")
    assert len(after) == 1
    assert after[0].run_id == before.run_id
    assert after[0].state is AgentRunState.COMPLETED
    assert len(messages) == 4


def test_admitted_message_edit_and_delete_fence_transcript_without_new_run(
    tmp_path: Path,
) -> None:
    # Given: a signed message has already produced one response Run.
    owner, _ = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1

    # When: Slack delivers a newer edit for the admitted source message.
    receive(
        owner,
        subtype="message_changed",
        user="U1",
        event_ts="100.002",
        message={
            "type": "message",
            "user": "U1",
            "text": "수정된 질문",
            "ts": "100.001",
            "edited": {"user": "U1", "ts": "100.002"},
        },
        previous_message={
            "type": "message",
            "user": "U1",
            "text": "<@UBOT> 첫 질문",
            "ts": "100.001",
        },
    )

    # Then: the transcript uses the canonical revision and no response job is created.
    conversation_id = _conversation_id(owner)
    transcript = owner.store.transcript(conversation_id)
    transcript_messages = _JSON_MESSAGES.validate_python(transcript["messages"])
    assert [item["user"] for item in transcript_messages] == ["수정된 질문"]
    assert not owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1

    # When: Slack deletes that same admitted source message.
    receive(
        owner,
        subtype="message_deleted",
        user="U1",
        event_ts="100.003",
        deleted_ts="100.001",
        previous_message={
            "type": "message",
            "user": "U1",
            "text": "수정된 질문",
            "ts": "100.001",
        },
    )

    # Then: deleted source text is immediately absent and still creates no new Run.
    assert owner.store.transcript(conversation_id)["messages"] == []
    assert not owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1


def _conversation_id(owner: SlackEvents) -> str:
    with owner.store.connect() as db:
        row = _STRING_ROW.validate_python(
            db.execute("SELECT conversation_id FROM slack_conversations").fetchone()
        )
    if row is None:
        message = "expected one admitted conversation"
        raise AssertionError(message)
    return TypeAdapter(tuple[str]).validate_python(row)[0]


@pytest.mark.parametrize(
    "fields",
    [
        {"channel": "C2"},
        {"user": "U2"},
        {"user": "UBOT"},
        {"bot_id": "B1"},
        {"subtype": "message_changed"},
        {"subtype": "file_share"},
        {"type": "message", "text": "일반 채널 대화"},
    ],
)
def test_unauthorized_or_unrelated_events_do_not_start_work(
    tmp_path: Path,
    fields: dict[str, str],
) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, **fields)
    assert not owner.work_once(now=NOW)
    assert not messages


def test_dm_is_scoped_and_cannot_use_shared_tools_or_history(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    reasoning = RecordingReasoning()
    service = owner.commands.application.service
    service.reasoning = reasoning
    service.registry = ToolRegistry((effect_descriptor(),))
    service.tools = {"capture.appium": ResearchAdapter()}
    owner = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    receive(owner, text="<@UBOT> 공유 비밀")
    assert owner.work_once(now=NOW)
    receive(
        owner, type="message", channel="D1", channel_type="im", text="비공개 질문", ts="100.002"
    )
    assert owner.work_once(now=NOW)
    private = reasoning.requests[-1]
    assert "공유 비밀" not in private.model_dump_json()
    assert "capture.appium" not in private.model_dump_json()
    assert messages[-1]["channel"] == "D1"
    assert "thread_ts" not in messages[-1]
    receive(owner, type="message", channel="D1", channel_type="im", text="이어 질문", ts="100.003")
    assert owner.work_once(now=NOW)
    assert "비공개 질문" in reasoning.requests[-1].model_dump_json()
    receive(owner, type="message", channel="D2", channel_type="im", text="별도 대화", ts="100.004")
    assert owner.work_once(now=NOW)
    assert "비공개 질문" not in reasoning.requests[-1].model_dump_json()
    assert len(service.repository.list_runs("team")) == 1


def test_close_reopen_and_queued_messages_are_ordered(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    receive(owner, type="message", text="종료", ts="100.002", thread_ts="100.001")
    receive(owner, type="message", text="닫힌 뒤 질문", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert owner.work_once(now=NOW)
    count = len(messages)
    assert owner.work_once(now=NOW)
    assert len(messages) == count
    receive(owner, type="message", text="무시", ts="100.004", thread_ts="100.001")
    assert not owner.work_once(now=NOW)
    receive(owner, type="message", text="다시 시작", ts="100.005", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="재개 질문", ts="100.006", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1


def test_http_challenge_signature_and_maintenance_boundary(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    marker = tmp_path / "maintenance"
    api = MarketingAgentApi(
        owner.commands.application.service,
        "team",
        "member",
        "secret",
        slack_events=owner,
        slack_only=True,
        maintenance=MaintenanceGate(marker, "test-sha"),
    )
    body, headers = signed({"type": "url_verification", "challenge": "proof"})
    response = api.dispatch(
        "POST", "/channels/slack/events", authorization=None, body=body, headers=headers, now=NOW
    )
    assert response.status == 200
    assert response.body == {"challenge": "proof"}
    assert (
        api.dispatch(
            "POST",
            "/channels/slack/events",
            authorization=None,
            body=body + b"x",
            headers=headers,
            now=NOW,
        ).status
        == 403
    )
    _ = marker.write_text("updating")
    body, headers = event()
    assert (
        api.dispatch(
            "POST",
            "/channels/slack/events",
            authorization=None,
            body=body,
            headers=headers,
            now=NOW,
        ).status
        == 503
    )
    assert not owner.work_once(now=NOW)
    assert not messages


def test_approval_requires_current_exact_hash_and_authorized_member(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.AWAITING_APPROVAL
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    text = str(messages[-1]["text"])
    invocation = ToolInvocation.model_validate_json(text.split("\n", 3)[3])
    digest = contract_sha256(invocation)
    receive(owner, type="message", text="승인 " + "0" * 64, ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    receive(owner, type="message", text="승인 " + digest, ts="100.004", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED


def test_lost_notifications_and_removed_user_never_send_again(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    calls: list[JsonObject] = []

    def lost(payload: JsonObject) -> JsonObject:
        calls.append(payload)
        raise TimeoutError

    owner.commands.sender = lost
    receive(owner)
    assert owner.work_once(now=NOW)
    owner.recover()
    assert not owner.work_once(now=NOW)
    assert len(calls) == 2
    receive(owner, type="message", text="후속", ts="100.002", thread_ts="100.001")
    owner.commands.allowed_user_ids = frozenset()
    assert owner.work_once(now=NOW)
    assert len(calls) == 2


def test_interrupted_create_replays_frozen_plan_without_duplicate_run(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    claimed = owner.store.claim()
    assert claimed is not None
    message, _ = claimed
    conversation = owner.store.conversation(message.conversation_id)
    assert conversation is not None
    plan = owner._plan(conversation, message)  # pyright: ignore[reportPrivateUsage]
    owner.store.save_plan(message, plan)
    identity = owner.identity(message.user_id)
    _ = owner._execute(conversation, message, plan, identity, now=NOW)  # pyright: ignore[reportPrivateUsage]
    restarted = replace(owner)
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1
    assert len(messages) == 2


def test_nonapprover_cannot_approve_and_dm_member_scope_is_separate(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    first = owner.identity("U1")
    owner.commands.application.store.put_identity(
        first.model_copy(
            update={
                "binding_id": "slack-T1-U2",
                "external_user_id": "U2",
                "member_id": "other",
                "can_approve": False,
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
    digest = str(messages[-1]["text"]).split("승인 ")[1].split("\n")[0]
    receive(
        owner, type="message", user="U2", text="승인 " + digest, ts="100.002", thread_ts="100.001"
    )
    assert owner.work_once(now=NOW)
    assert service.repository.list_runs("team")[0].state is AgentRunState.AWAITING_APPROVAL
    reasoning = RecordingReasoning()
    service.reasoning = reasoning
    owner = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    receive(owner, type="message", channel="D1", channel_type="im", text="U1 전용", ts="100.003")
    assert owner.work_once(now=NOW)
    receive(
        owner,
        type="message",
        channel="D1",
        channel_type="im",
        user="U2",
        text="U2 전용",
        ts="100.004",
    )
    assert owner.work_once(now=NOW)
    assert "U1 전용" not in reasoning.requests[-1].model_dump_json()


def test_interrupted_continuation_replays_through_canonical_idempotency(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    owner.commands.application.service.reasoning = AskThenStopReasoning()
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="대학생", ts="100.002", thread_ts="100.001")
    claimed = owner.store.claim()
    assert claimed is not None
    message, _ = claimed
    conversation = owner.store.conversation(message.conversation_id)
    assert conversation is not None
    plan = owner._plan(conversation, message)  # pyright: ignore[reportPrivateUsage]
    assert plan.action == "revise"
    owner.store.save_plan(message, plan)
    owner.recover()
    assert owner.work_once(now=NOW)
    assert "completed" in str(messages[-1]["text"])
    assert (
        owner.commands.application.service.repository.list_runs("team")[0].state
        is AgentRunState.COMPLETED
    )
    assert not owner.work_once(now=NOW)


@pytest.mark.parametrize("fields", [{"api_app_id": "A2"}, {"team_id": "T2"}])
def test_signed_envelope_must_match_the_installed_workspace(
    tmp_path: Path,
    fields: JsonObject,
) -> None:
    owner, _ = setup_events(tmp_path)
    body, headers = signed(
        {"type": "event_callback", "api_app_id": "A1", "team_id": "T1", **fields}
    )
    with pytest.raises(ValueError, match="scope_rejected"):
        _ = owner.receive(body, headers, now=NOW)
    assert not owner.work_once(now=NOW)
