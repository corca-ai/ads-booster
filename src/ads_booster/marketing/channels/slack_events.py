"""Signed Events API: one agent, scoped threads/DMs, and durable asynchronous replies."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from threading import Event, Thread
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.execution_control import ExecutionCancelledError, ExecutionControl, execution_scope
from ads_booster.marketing.agent_core.registry import CapabilityPolicy
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.channels.contracts import ChannelIdentityBinding, ChannelKind
from ads_booster.marketing.channels.github_results import issue_results
from ads_booster.marketing.channels.slack_commands import SlackCommands
from ads_booster.marketing.channels.slack_conversations import (
    Conversation,
    Message,
    MessagePlan,
    SlackConversationStore,
)
from ads_booster.marketing.channels.slack_images import SlackImageDelivery
from ads_booster.marketing.channels.slack_progress import SlackProgressStore
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

_PROGRESS_INTERVAL_SECONDS = 5
_MAX_CHALLENGE = 4096
_MAX_MESSAGE = 8000
_MAX_FIELD = 100000
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_TIMESTAMP = re.compile(r"[0-9]{1,16}\.[0-9]{1,8}")
_HASH = re.compile(r"[a-f0-9]{64}")
_HELP = """채널에서는 저를 멘션해 시작하고, 같은 스레드에 답글을 보내 이어가세요.
DM에서도 텍스트로 대화할 수 있습니다. DM은 공개 검색과 답변만 지원합니다.
상태 — 현재 작업 확인
검토 1 — 승인할 전체 내용 확인 (페이지 번호 변경 가능)
승인 승인해시 / 거절 승인해시 — 정확한 실행 승인 또는 거절
계속 — 중단된 실행의 안전한 재개 시도
종료 — 이 대화의 자동 응답 종료 (이미 실행 중인 작업을 강제 취소하지 않음)
다시 시작 — 닫힌 대화 재개
첨부파일 내용은 읽지 않습니다. 필요한 내용을 텍스트로 보내주세요."""


@dataclass(slots=True)
class SlackEvents:
    commands: SlackCommands
    bot_user_id: str
    channel_ids: frozenset[str]
    allow_dm: bool = True
    image_delivery: SlackImageDelivery | None = None
    store: SlackConversationStore = field(init=False)
    private_service: MarketingAgentService = field(init=False)
    progress: SlackProgressStore = field(init=False)

    def __post_init__(self) -> None:
        """Share the canonical ledger while constraining private tool authority."""
        if not re.fullmatch(r"[UW][A-Z0-9]+", self.bot_user_id):
            raise ValueError("slack_bot_user_id_invalid")
        self.store = SlackConversationStore(self.commands.application.store.database_path)
        self.progress = SlackProgressStore(self.store)
        self.private_service = replace(
            self.commands.application.service,
            capability_policy=CapabilityPolicy(allowed_capability_ids=("research.search",)),
        )
        self.private_service.execution_lock = self.commands.application.service.execution_lock

    def identity(self, user_id: str) -> ChannelIdentityBinding:
        installation = self.commands.application.store.resolve_installation(
            ChannelKind.SLACK, self.commands.team_id
        )
        identity = self.commands.application.store.resolve_identity(
            installation.installation_id, user_id
        )
        if (
            not installation.enabled
            or not identity.can_create_runs
            or identity.revoked_at is not None
            or user_id not in self.commands.allowed_user_ids
        ):
            raise ValueError("slack_user_not_allowed")
        return identity

    def receive(self, body: bytes, headers: dict[str, str], *, now: datetime) -> JsonObject:
        self.commands.verifier.verify(
            body,
            timestamp=headers.get("x-slack-request-timestamp", ""),
            signature=headers.get("x-slack-signature", ""),
            now=now,
        )
        envelope = _JSON.validate_json(body)
        if envelope.get("type") == "url_verification":
            challenge = _string(envelope, "challenge")
            if len(challenge) > _MAX_CHALLENGE:
                raise ValueError("slack_challenge_invalid")
            return {"challenge": challenge}
        if (
            envelope.get("api_app_id") != self.commands.app_id
            or envelope.get("team_id") != self.commands.team_id
        ):
            raise ValueError("slack_event_scope_rejected")
        if envelope.get("type") != "event_callback" or envelope.get("is_ext_shared_channel"):
            return {"ok": True}
        event = envelope.get("event")
        if not isinstance(event, dict):
            raise ValueError("slack_event_missing")
        kind = event.get("type")
        if kind not in {"message", "app_mention"} or event.get("bot_id") or event.get("subtype"):
            return {"ok": True}
        user_id = _string(event, "user")
        if user_id == self.bot_user_id:
            return {"ok": True}
        try:
            identity = self.identity(user_id)
        except ValueError:
            return {"ok": True}
        admitted = self._message(event, identity)
        if admitted is not None:
            conversation, message = admitted
            self.store.admit(conversation, message)
        return {"ok": True}

    def _message(  # noqa: C901,PLR0911 - explicit channel and DM admission boundaries.
        self, event: JsonObject, identity: ChannelIdentityBinding
    ) -> tuple[Conversation, Message] | None:
        channel = _string(event, "channel")
        text = _string(event, "text", default="")
        if len(text) > _MAX_MESSAGE:
            raise ValueError("slack_message_too_long")
        ts = _string(event, "ts")
        thread = _string(event, "thread_ts", default="")
        if not _TIMESTAMP.fullmatch(ts) or (thread and not _TIMESTAMP.fullmatch(thread)):
            raise ValueError("slack_timestamp_invalid")
        private = event.get("channel_type") == "im" and channel.startswith("D")
        mentioned = f"<@{self.bot_user_id}>" in text
        if private:
            if not self.allow_dm:
                return None
            thread = thread or ""  # Unthreaded DM messages share the user's DM session.
        else:
            if channel not in self.channel_ids:
                return None
            # Slack can deliver both message and app_mention for the same message.
            if event.get("type") == "message" and mentioned:
                return None
            if event.get("type") == "app_mention" and not mentioned:
                return None
            thread = thread or ts
        scope = contract_sha256(
            {
                "team": self.commands.team_id,
                "workspace": identity.tenant_id,
                "channel": channel,
                "thread": thread,
                "member": identity.member_id if private else "",
            }
        )
        conversation_id = f"slack-conversation-{scope}"
        current = self.store.conversation(conversation_id)
        if current is None and not private and not mentioned:
            return None  # Ordinary channel chatter never starts a conversation.
        text = text.replace(f"<@{self.bot_user_id}>", "").strip()
        reopens = text in {"다시 시작", "reopen"}
        if current is not None and current.closed and not reopens:
            return None
        if not text:
            text = "도움말"
        conversation = current or Conversation(
            conversation_id=conversation_id,
            tenant_id=f"slack-private-{scope}" if private else identity.tenant_id,
            channel_id=channel,
            thread_ts=thread,
            owner_id=identity.member_id if private else "",
            private=private,
        )
        message = Message(
            message_id="slack-message-"
            + contract_sha256({"team": self.commands.team_id, "channel": channel, "ts": ts}),
            conversation_id=conversation_id,
            user_id=identity.external_user_id,
            text=text,
            reopens=reopens,
        )
        return conversation, message

    def recover(self) -> None:
        self.store.recover()

    def _service(self, conversation: Conversation) -> MarketingAgentService:
        return self.private_service if conversation.private else self.commands.application.service

    def _authorize(self, conversation: Conversation, user_id: str) -> ChannelIdentityBinding:
        identity = self.identity(user_id)
        if conversation.private:
            if not self.allow_dm or conversation.owner_id != identity.member_id:
                raise ValueError("slack_private_scope_denied")
        elif (
            conversation.channel_id not in self.channel_ids
            or identity.tenant_id != conversation.tenant_id
        ):
            raise ValueError("slack_channel_removed")
        return identity

    def work_once(self, *, now: datetime) -> bool:
        if self._notify():
            return True
        claimed = self.store.claim()
        if claimed is None:
            return False
        message, plan = claimed
        conversation = self.store.conversation(message.conversation_id)
        if conversation is None:
            raise ValueError("slack_conversation_missing")
        try:
            identity = self._authorize(conversation, message.user_id)
            service = self._service(conversation)
            # The same lock owns plan binding, Run mutation and thread progression.
            with service.execution_lock:
                if plan is None:
                    plan = self._plan(conversation, message)
                    self.store.save_plan(message, plan)
                if plan.action in {"create", "input", "resume", "approve"}:
                    with self._working(conversation, message, plan) as control:
                        try:
                            result = self._execute(conversation, message, plan, identity, now=now)
                        except ExecutionCancelledError:
                            result = self._cancelled_result(conversation, plan, now=now)
                        else:
                            if control.cancelled():
                                result = self._cancelled_result(conversation, plan, now=now)
                else:
                    result = self._execute(conversation, message, plan, identity, now=now)
                self.store.finish(message, result)
        except Exception:  # noqa: BLE001 - never repeat an uncertain effect; no provider secrets.
            self.store.finish(
                message,
                " ".join(  # noqa: FLY002 - readable translated message.
                    (
                        "처리를 완료하지 못했습니다. 이 대화에 '상태'를 보내 확인하세요.",
                        "승인 시에는 현재 '검토 1'에 표시된 해시를 사용하세요.",
                    )
                ),
                blocked=True,
            )
        _ = self._notify()
        return True

    def interact(self, body: bytes, headers: dict[str, str], *, now: datetime) -> JsonObject:
        """Persist cancellation without waiting for the active execution lock or Slack API."""
        self.commands.verifier.verify(
            body,
            timestamp=headers.get("x-slack-request-timestamp", ""),
            signature=headers.get("x-slack-signature", ""),
            now=now,
        )
        form = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=2)
        if set(form) != {"payload"} or len(form["payload"]) != 1:
            raise ValueError("slack_interaction_form_invalid")
        payload = _JSON.validate_json(form["payload"][0])
        team, user, container = payload.get("team"), payload.get("user"), payload.get("container")
        actions = payload.get("actions")
        if (
            payload.get("type") != "block_actions"
            or payload.get("api_app_id") != self.commands.app_id
            or not isinstance(team, dict)
            or team.get("id") != self.commands.team_id
            or not isinstance(user, dict)
            or not isinstance(container, dict)
            or not isinstance(actions, list)
            or len(actions) != 1
            or not isinstance(actions[0], dict)
            or actions[0].get("action_id") != "trace_stop_run"
        ):
            raise ValueError("slack_interaction_scope_rejected")
        record = self.progress.locate(_string(actions[0], "value"))
        if (
            record is None
            or container.get("channel_id") != record.channel_id
            or container.get("message_ts") != record.timestamp
            or not record.timestamp
        ):
            raise ValueError("slack_interaction_message_rejected")
        conversation = self.store.conversation(record.conversation_id)
        if conversation is None:
            raise ValueError("slack_conversation_missing")
        actor = _string(user, "id")
        identity = self._authorize(conversation, actor)
        if actor != record.user_id and (conversation.private or not identity.can_approve):
            raise ValueError("slack_cancel_not_allowed")
        self.progress.cancel(record.message_id)
        return {}

    def _cancelled_result(
        self, conversation: Conversation, plan: MessagePlan, *, now: datetime
    ) -> str:
        service = self._service(conversation)
        run = service.stop(conversation.tenant_id, plan.run_id, now=now)
        if run is not None and run.state is AgentRunState.AWAITING_RECONCILIATION:
            return "후속 실행을 멈췄습니다. 이미 요청한 외부 작업의 결과는 확인이 필요합니다."
        if run is not None and run.state is AgentRunState.COMPLETED:
            return self.summary(conversation.model_copy(update={"current_run": plan.run_id}))
        result = "실행을 중단했습니다. 새 요청을 보내면 다시 시작합니다."
        if run is not None:
            result += "\n" + issue_results(
                service.repository.records(conversation.tenant_id, plan.run_id)
            )
        return result

    @contextmanager
    def _working(
        self, conversation: Conversation, message: Message, plan: MessagePlan
    ) -> Generator[ExecutionControl]:
        self.progress.begin(message.message_id, plan.run_id, conversation.channel_id)
        control = ExecutionControl(lambda: self.progress.cancelled(message.message_id))
        stopped = Event()
        started = time.monotonic()
        if self.store.claim_ack(message):
            state = self._status(conversation, message, control.stage, initial=True)
            self.store.sent(message, state, ack=True)

        def refresh() -> None:
            while not stopped.wait(_PROGRESS_INTERVAL_SECONDS):
                try:
                    stage = (
                        "중단 요청을 처리하고 있습니다" if control.cancelled() else control.stage
                    )
                    _ = self._status(
                        conversation, message, f"{stage} · {int(time.monotonic() - started)}초 경과"
                    )
                except Exception:  # noqa: BLE001,S110 - status failure cannot retry agent work.
                    pass

        thread = Thread(target=refresh, name="trace-slack-progress", daemon=True)
        thread.start()
        try:
            with execution_scope(control):
                yield control
        finally:
            stopped.set()
            thread.join()

    def _status(
        self, conversation: Conversation, message: Message, text: str, *, initial: bool = False
    ) -> str:
        record = self.progress.locate(message.message_id)
        if record is None or (not initial and not record.timestamp):
            return "skipped"
        payload = self._payload(conversation, "⏳ " + text)
        blocks = payload["blocks"]
        if isinstance(blocks, list) and not record.cancelled:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "실행 중단"},
                            "action_id": "trace_stop_run",
                            "value": message.message_id,
                            "style": "danger",
                        }
                    ],
                }
            )
        if record.timestamp:
            payload["ts"] = record.timestamp
            _ = payload.pop("thread_ts", None)
        try:
            response = self.commands.sender(payload)
            if response.get("ok") is True and isinstance(response.get("ts"), str):
                self.progress.sent(message.message_id, str(response["ts"]))
                return "delivered"
            return "failed"  # noqa: TRY300 - explicit Slack success/rejection.
        except Exception:  # noqa: BLE001 - no duplicate initial status post on uncertain send.
            return "unknown"

    def _plan(self, conversation: Conversation, message: Message) -> MessagePlan:  # noqa: C901,PLR0911 - freeze one of the supported conversation actions.
        text = message.text
        action, _, argument = text.partition(" ")
        if conversation.closed and not message.reopens:
            return MessagePlan(action="reply", reply="")
        if text in {"종료", "close"}:
            return MessagePlan(action="close")
        if text in {"도움말", "help", "다시 시작", "reopen"}:
            return MessagePlan(action="reply", reply=_HELP)
        service = self._service(conversation)
        run = service.repository.get(conversation.tenant_id, conversation.current_run)
        if text in {"상태", "status"}:
            return MessagePlan(action="reply", reply=self.summary(conversation))
        if action in {"검토", "review"}:
            reply = self.commands.review(
                conversation.tenant_id, conversation.current_run, int(argument or "1")
            )
            return MessagePlan(action="reply", reply=reply)
        if action in {"승인", "approve", "거절", "reject"}:
            if run is None or not _HASH.fullmatch(argument):
                return MessagePlan(
                    action="reply",
                    reply="'검토 1'을 읽고 '승인 해시' 또는 '거절 해시'를 보내세요.",
                )
            return MessagePlan(
                action="approve" if action in {"승인", "approve"} else "reject",
                run_id=run.run_id,
                digest=argument,
            )
        if text in {"계속", "resume"}:
            return MessagePlan(action="resume", run_id=conversation.current_run)
        context = self.store.transcript(conversation.conversation_id)
        context["privacy"] = "private_dm" if conversation.private else "shared_thread"
        if run is not None and run.state is AgentRunState.AWAITING_INPUT:
            return MessagePlan(
                action="input",
                run_id=run.run_id,
                revision=run.revision,
                evidence={
                    "note": text,
                    "slack_message_id": message.message_id,
                    "conversation": context,
                },
            )
        if run is not None and run.state not in {
            AgentRunState.COMPLETED,
            AgentRunState.STOPPED,
            AgentRunState.FAILED,
        }:
            return MessagePlan(action="reply", reply=self.summary(conversation))
        return MessagePlan(
            action="create",
            run_id="slack-talk-"
            + contract_sha256(
                {"message": message.message_id, "scope": conversation.conversation_id}
            )[:40],
            goal=AgentGoal(
                objective=text,
                success_criteria=("대화 맥락을 이어받아 출처와 불확실성이 명확한 답변을 만든다.",),
                context={"slack_conversation": context},
            ),
        )

    def _execute(  # noqa: C901 - one canonical mutation per frozen action.
        self,
        conversation: Conversation,
        message: Message,
        plan: MessagePlan,
        identity: ChannelIdentityBinding,
        *,
        now: datetime,
    ) -> str:
        service = self._service(conversation)
        if message.reopens:
            conversation = conversation.model_copy(update={"closed": False})
        if plan.action == "close":
            self.store.update_conversation(conversation.model_copy(update={"closed": True}))
            return "이 대화의 자동 응답을 종료했습니다. '다시 시작'을 보내면 재개합니다."
        if plan.action == "reply":
            self.store.update_conversation(conversation)
            return plan.reply
        conversation = conversation.model_copy(update={"current_run": plan.run_id})
        self.store.update_conversation(conversation)
        if plan.action == "create":
            if plan.goal is None:
                raise ValueError("slack_goal_missing")
            _ = service.create(
                CreateAgentRunRequest(
                    run_id=plan.run_id,
                    tenant_id=conversation.tenant_id,
                    goal=plan.goal,
                    budget=AgentBudget(max_tool_calls=8, max_cost_units=50),
                ),
                now=now,
            )
        elif plan.action == "input":
            run = service.repository.get(conversation.tenant_id, plan.run_id)
            if run is None or run.revision != plan.revision or plan.evidence is None:
                raise ValueError("slack_input_revision_changed")
            _ = service.submit_input(conversation.tenant_id, plan.run_id, plan.evidence, now=now)
        elif plan.action in {"approve", "reject"}:
            if not identity.can_approve or conversation.private:
                raise ValueError("slack_approval_not_allowed")
            _ = service.decide_approval(
                conversation.tenant_id,
                plan.run_id,
                approver_id=identity.member_id,
                granted=plan.action == "approve",
                expected_invocation_sha256=plan.digest,
                expires_at=now + timedelta(minutes=5) if plan.action == "approve" else None,
                now=now,
            )
        elif plan.action == "resume":
            _ = service.drive(conversation.tenant_id, plan.run_id, now=now)
        return self.summary(conversation)

    def summary(self, conversation: Conversation) -> str:
        service = self._service(conversation)
        run = service.repository.get(conversation.tenant_id, conversation.current_run)
        if run is None:
            return "아직 시작한 작업이 없습니다. 요청을 텍스트로 보내주세요."
        records = service.repository.records(conversation.tenant_id, run.run_id)
        if run.state is AgentRunState.AWAITING_APPROVAL:
            invocation = next(
                ToolInvocation.model_validate(r.payload)
                for r in reversed(records)
                if r.kind is AgentRecordKind.INVOCATION
            )
            return (
                "실행 전 승인이 필요합니다. '검토 1'로 전체 내용을 확인하세요.\n"
                f"승인 {contract_sha256(invocation)}\n"
                "거절하려면 '거절' 다음에 같은 해시를 붙여 보내세요."
            )
        latest = next((r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None)
        decision = None if latest is None else latest.payload.get("decision")
        answer = str(decision.get("reasoning_summary", "")) if isinstance(decision, dict) else ""
        return f"{answer}\n{issue_results(records)}\n\n상태: {run.state.value}\n실행: {run.run_id}"

    def _payload(self, conversation: Conversation, text: str) -> JsonObject:
        payload: JsonObject = {
            "channel": conversation.channel_id,
            "text": text,
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": [
                {"type": "section", "text": {"type": "plain_text", "text": text[i : i + 2500]}}
                for i in range(0, len(text), 2500)
            ],
        }
        if conversation.thread_ts:
            payload["thread_ts"] = conversation.thread_ts
        return payload

    def _send(self, conversation: Conversation, text: str, *, timestamp: str = "") -> str:
        if not text:
            return "skipped"
        payload = self._payload(conversation, text)
        if timestamp:
            payload["ts"] = timestamp
            _ = payload.pop("thread_ts", None)
        try:
            response = self.commands.sender(payload)
            return "delivered" if response.get("ok") is True and response.get("ts") else "failed"
        except Exception:  # noqa: BLE001 - write-ahead outbox forbids retry after lost response.
            return "unknown"

    def _notify(self) -> bool:
        claimed = self.store.claim_notification()
        if claimed is None:
            return False
        message, result = claimed
        conversation = self.store.conversation(message.conversation_id)
        if conversation is None:
            self.store.sent(message, "denied")
            return True
        try:
            _ = self._authorize(conversation, message.user_id)
        except ValueError:
            state = "denied"
        else:
            if self.image_delivery is not None and not conversation.private:
                plan = self.progress.locate(message.message_id)
                if plan is not None:
                    result += "\n" + self.image_delivery.deliver(
                        self._service(conversation).repository.records(
                            conversation.tenant_id, plan.run_id
                        ),
                        conversation,
                    )
            status = self.progress.locate(message.message_id)
            state = self._send(
                conversation, result, timestamp="" if status is None else status.timestamp
            )
        self.store.sent(message, state)
        return True


def _string(value: JsonObject, key: str, *, default: str | None = None) -> str:
    result = value.get(key, default)
    if not isinstance(result, str) or (default is None and not result) or len(result) > _MAX_FIELD:
        raise ValueError("slack_event_field_invalid")
    return result


def events_from_env(env: Mapping[str, str], commands: SlackCommands | None) -> SlackEvents | None:
    bot_user_id = env.get("TRACE_MARKETING_SLACK_BOT_USER_ID")
    if not bot_user_id:
        return None
    if commands is None:
        raise ValueError("slack_events_require_installation")
    channels = frozenset(
        filter(
            None,
            (
                value.strip()
                for value in env.get(
                    "TRACE_MARKETING_SLACK_ALLOWED_CHANNEL_IDS", commands.channel_id
                ).split(",")
            ),
        )
    )
    if not channels or any(not re.fullmatch(r"[CG][A-Z0-9]+", value) for value in channels):
        raise ValueError("slack_allowed_channels_invalid")
    return SlackEvents(
        commands,
        bot_user_id,
        channels,
        allow_dm=env.get("TRACE_MARKETING_SLACK_ALLOW_DM", "1") == "1",
        image_delivery=SlackImageDelivery(
            commands.application.store.database_path.parent / "images",
            commands.application.store.database_path,
            env.get("TRACE_MARKETING_SLACK_BOT_TOKEN", ""),
        )
        if env.get("TRACE_MARKETING_SLACK_BOT_TOKEN")
        else None,
    )
