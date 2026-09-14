"""Canonical asynchronous updates reuse the Slack write-ahead outbox."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.drive_work import DriveOrigin
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.channels.slack_conversations import Conversation, Message
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.channels.task_results import result_for
from tests.marketing.agent_service.test_creative_image_edit import approve, setup
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def test_run_update_is_durable_same_thread_and_once(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 배경 검토해줘")
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    before = len(messages)
    assert owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    assert not owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    owner.recover()
    assert owner.work_once(now=NOW)
    assert len(messages) == before + 1
    assert messages[-1]["thread_ts"] == "100.001"
    assert not owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    assert not owner.work_once(now=NOW)
    assert not owner.enqueue_run_update("other", run.run_id, event_id="worker-operation")


def test_claimed_notification_is_not_resent_after_response_loss(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 배경 검토해줘")
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    claimed = owner.store.claim_notification()
    assert claimed is not None
    count = len(messages)
    owner.recover()
    assert not owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    assert not owner.work_once(now=NOW)
    assert len(messages) == count
    api = MarketingAgentApi(owner.commands.application.service, "team", "member", "secret")
    response = api.dispatch("GET", f"/v1/runs/{run.run_id}", authorization="Bearer secret", now=NOW)
    assert isinstance(response.body, dict)
    task = response.body["task"]
    assert isinstance(task, dict)
    assert task["disposition"] == "satisfied"
    deliveries = response.body["delivery"]
    assert isinstance(deliveries, list)
    assert any(isinstance(item, dict) and item.get("state") == "unknown" for item in deliveries)


def test_notification_rechecks_current_member_and_excludes_synthetic_user_context(
    tmp_path: Path,
) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 배경 검토해줘")
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    conversation = owner.store.conversation_for_run("team", run.run_id)
    assert conversation is not None
    context = owner.store.transcript(conversation.conversation_id)
    assert owner.enqueue_run_update("team", run.run_id, event_id="worker-operation")
    assert owner.store.transcript(conversation.conversation_id) == context
    count = len(messages)
    revoked = replace(owner, commands=replace(owner.commands, allowed_user_ids=frozenset()))
    assert revoked.work_once(now=NOW)
    assert len(messages) == count
    owner.store.update_conversation(conversation.model_copy(update={"closed": True}))
    assert not owner.enqueue_run_update("team", run.run_id, event_id="another-operation")


def test_image_edit_completion_projects_to_slack_outbox_and_recovers_callback_loss(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    coordinator, provider = setup(remote)
    approve(coordinator)
    config = tmp_path / "slack-remote.json"
    _ = config.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "tenant-a",
                "members": [{"slack_user_id": "U1", "member_id": "member", "can_approve": True}],
            }
        )
    )
    commands = slack_from_env(
        {
            "TRACE_MARKETING_SLACK_INSTALLATION": str(config),
            "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-secret",
            "TRACE_MARKETING_PUBLIC_ORIGIN": "https://agent.example",
            "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture",
            "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
        },
        coordinator.service,
    )
    assert commands is not None
    sent: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        sent.append(payload)
        return {"ok": True, "ts": "123.456"}

    commands.sender = send
    events = SlackEvents(commands, "UBOT", frozenset({"C1"}))
    conversation = Conversation(
        conversation_id="fixture-thread",
        tenant_id="tenant-a",
        channel_id="C1",
        thread_ts="100.001",
        owner_id="",
        private=False,
        current_run="run-one",
    )
    message = Message(
        message_id="fixture-request",
        conversation_id=conversation.conversation_id,
        user_id="U1",
        text="승인된 이미지 편집",
    )
    events.store.admit(conversation, message)
    events.drive_queue.bind(
        DriveOrigin(
            tenant_id="tenant-a",
            run_id="run-one",
            channel="slack",
            principal_id="U1",
            event_id=message.message_id,
            conversation_id=conversation.conversation_id,
        )
    )
    events.store.finish(message, "")
    events.store.sent(message, "skipped")
    events.store.sent(message, "skipped", ack=True)
    failure = True

    def callback(tenant: str, run: str, event: str) -> None:
        _ = events.enqueue_run_update(tenant, run, event_id=event)
        if failure:
            detail = "fixture callback response lost after durable enqueue"
            raise RuntimeError(detail)

    coordinator.on_completed = callback
    with pytest.raises(RuntimeError, match="response lost"):
        _ = coordinator.work_once()
    events.recover()
    failure = False
    assert replace(coordinator).work_once()["state"] == "running"
    for _ in range(5):
        _ = events.work_once(now=NOW)
    assert len(sent) == 1
    assert sent[0]["thread_ts"] == "100.001"
    run = coordinator.service.repository.get("tenant-a", "run-one")
    assert run is not None
    accepted = result_for(run, coordinator.service.repository.records("tenant-a", "run-one"))
    assert accepted.identity is not None
    assert sent[0]["text"] == accepted.text
    assert replace(coordinator).work_once()["state"] == "idle"
    assert not events.work_once(now=NOW)
    assert len(sent) == 1
    assert provider.calls == 1
