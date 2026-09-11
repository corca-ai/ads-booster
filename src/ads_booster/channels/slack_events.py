"""Signed Events API: one agent, scoped threads/DMs, and durable asynchronous replies."""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import CapabilityPolicy
from ads_booster.agent.service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.agent.service.knowledge import READ_ONLY_DM_TOOLS
from ads_booster.agent.service.knowledge_ingress import (
    KnowledgeIngressSink,
    PendingKnowledgeIngress,
)
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.channels.contracts import ChannelIdentityBinding, ChannelKind
from ads_booster.channels.github_results import issue_results
from ads_booster.channels.knowledge_ingress_slack import (
    SlackIngressRequest,
    build_slack_ingress,
    build_slack_ingresses,
    slack_revision,
)
from ads_booster.channels.slack_approval import (
    approve_requested_creation,
    current_approval_source,
    proposal_text,
)
from ads_booster.channels.slack_attachments import (
    SlackAttachmentContext,
    attachment_capabilities,
    attachment_references,
)
from ads_booster.channels.slack_commands import SlackCommands
from ads_booster.channels.slack_conversations import (
    Conversation,
    Message,
    MessagePlan,
    SlackConversationStore,
)
from ads_booster.channels.slack_creative_setup import connect_slack_creative
from ads_booster.channels.slack_delivery import delivery_command
from ads_booster.channels.slack_image_review import bind_files
from ads_booster.channels.slack_images import SlackImageDelivery
from ads_booster.channels.slack_learning_questions import (
    has_learning_correction_signal,
    is_ambiguous_learning_affirmation,
    learning_question_answer,
)
from ads_booster.channels.slack_memory import memory_command
from ads_booster.channels.slack_performance import (
    is_performance_command,
    performance_command,
)
from ads_booster.channels.slack_progress import SlackProgressStore
from ads_booster.channels.slack_work_observations import (
    is_work_observation_command,
    work_observation_command,
)
from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.execution_control import ExecutionCancelledError, ExecutionControl, execution_scope
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    ConversationEventKind,
    Provenance,
)
from ads_booster.knowledge.contracts import AuthenticatedEvent
from ads_booster.knowledge.errors import AccessDeniedError
from ads_booster.knowledge.questions import QuestionError
from ads_booster.knowledge.tool_contracts import TrustedQuestionAnswer
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from ads_booster.agent.service.knowledge_ingress import TrustedLearningSource
    from ads_booster.knowledge.tool_contracts import QuestionRecord, TrustedInvocationContext
    from ads_booster.knowledge.tools import ToolHost

_PROGRESS_INTERVAL_SECONDS = 5
_MAX_CHALLENGE = 4096
_MAX_MESSAGE = 8000
_MAX_FIELD = 100000
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_TIMESTAMP = re.compile(r"[0-9]{1,16}\.[0-9]{1,8}")
_HASH = re.compile(r"[a-f0-9]{64}")
_LOGGER = logging.getLogger(__name__)
_FAILURE_REPLIES = {
    "slack_approval_source_changed": (
        "승인 메시지가 수정되거나 삭제되어 실행하지 않았습니다. 현재 의사를 새 메시지로 알려주세요."
    ),
    "slack_approval_not_allowed": (
        "이 대화에서 승인 권한이 없습니다. 승인 가능한 팀원에게 검토를 요청하세요."
    ),
    "agent_approval_invocation_changed": (
        "보낸 해시가 현재 승인안과 다릅니다. '검토 1'로 현재 내용을 확인하세요."
    ),
    "slack_production_target_changed": (
        "승인 대상 작업이 변경되었습니다. '검토 1'로 현재 내용을 확인하세요."
    ),
    "agent_run_not_awaiting_approval": (
        "현재 승인 대기 상태가 아닙니다. '상태'로 작업 진행 상황을 확인하세요."
    ),
    "tool_dispatch_no_longer_available": (
        "승인 대상 도구를 현재 사용할 수 없습니다. "
        "운영자에게 도구 연결과 준비 상태 확인을 요청하세요."
    ),
    "tool_dispatch_adapter_unavailable": (
        "승인 대상 도구를 현재 사용할 수 없습니다. "
        "운영자에게 도구 연결과 준비 상태 확인을 요청하세요."
    ),
}


@dataclass(frozen=True, slots=True)
class _LearningQuestionAnswerContext:
    source: TrustedLearningSource
    context: TrustedInvocationContext
    pending: tuple[QuestionRecord, ...]
    host: ToolHost


_STATUS_TEXTS = frozenset(
    {"상태", "status", "어디까지 됐어", "어디까지 됐어요", "진행 상황", "진행상황 알려줘"}
)
_HELP = """채널에서는 저를 멘션해 시작하고, 같은 스레드에 답글을 보내 이어가세요.
독립 업무는 새 작업 요청내용으로 시작할 수 있습니다.
DM에서도 텍스트로 대화할 수 있습니다. DM은 공개 검색과 답변만 지원합니다.
상태 / 어디까지 됐어? — 현재 작업 확인
잠깐 멈춰줘 — 다음 작업을 멈추고 사람 입력 대기
수정·사람 작업 결과는 같은 업무에서 이어받습니다.
작업 기록 제작 12분 설명 / 작업 요약 — 사람이 들인 시간을 업무에 기록
성과 도움말 — 게시물별 사람이 보고한 수치 기록·정정·비교·학습
검토 1 — 승인할 전체 내용 확인 (페이지 번호 변경 가능)
승인 승인해시 / 거절 승인해시 — 정확한 실행 승인 또는 거절
계속 — 중단된 실행의 안전한 재개 시도
종료 — 이 대화의 자동 응답 종료 (이미 실행 중인 작업을 강제 취소하지 않음)
다시 시작 — 닫힌 대화 재개
첨부파일은 업무에 연결합니다.
실제 이미지 확인은 파일 접근과 시각 도구가 준비된 경우에만 수행합니다."""


@dataclass(slots=True)
class SlackEvents:
    commands: SlackCommands
    bot_user_id: str
    channel_ids: frozenset[str]
    allow_dm: bool = True
    knowledge_sink: KnowledgeIngressSink | None = None
    image_delivery: SlackImageDelivery | None = None
    workspace_mentions: bool = False
    store: SlackConversationStore = field(init=False)
    private_service: MarketingAgentService = field(init=False)
    progress: SlackProgressStore = field(init=False)

    def __post_init__(self) -> None:
        """Share the canonical ledger while constraining private tool authority."""
        if not re.fullmatch(r"[UW][A-Z0-9]+", self.bot_user_id):
            raise ValueError("slack_bot_user_id_invalid")
        self.store = SlackConversationStore(
            self.commands.application.store.database_path,
            knowledge_sink=self.knowledge_sink,
            installed_knowledge_ingress=(
                None
                if self.commands.application.service.knowledge is None
                else self.commands.application.service.knowledge.ingress
            ),
        )
        self.progress = SlackProgressStore(self.store)
        self.private_service = replace(
            self.commands.application.service,
            capability_policy=CapabilityPolicy(
                allowed_capability_ids=(
                    "research.search",
                    "marketing.analyze",
                    "skills.list",
                    "skills.read",
                    *sorted(READ_ONLY_DM_TOOLS),
                )
            ),
        )
        self.private_service.execution_lock = self.commands.application.service.execution_lock
        self.commands.application.service.boundary_signal = self._pending_steering
        self.private_service.boundary_signal = self._pending_steering
        self.commands.application.service.current_context = self._current_context
        self.private_service.current_context = self._current_context

    def _current_context(self, run: AgentRun, now: datetime) -> JsonObject | None:
        """Reproject scoped dialogue at each turn, including our own previous answers.

        Same-Run continuations do not rebuild AgentGoal. Read current inbox history
        here rather than storing another stale copy in continuation evidence.
        """
        conversation = self.store.conversation_for_run(run.tenant_id, run.run_id)
        if conversation is None:
            return None
        dialogue = self.store.transcript(conversation.conversation_id)
        memory = self._current_memory(run, now)
        if not dialogue["messages"] and memory is None:
            return None
        return {
            "schema_version": "trace.current-slack-context.v1",
            "role": "data",
            "dialogue": dialogue,
            "memory": memory,
            "authority": "conversation_only_not_verification_or_approval",
        }

    def _current_memory(self, run: AgentRun, now: datetime) -> JsonObject | None:
        conversation = self.store.conversation_for_run(run.tenant_id, run.run_id)
        if conversation is None:
            return None
        query = run.goal.objective
        for record in reversed(
            self._service(conversation).repository.records(run.tenant_id, run.run_id)
        ):
            if record.payload_schema_version == "trace.work-continuation.v1":
                note = record.payload.get("note")
                if isinstance(note, str):
                    query = note + " " + query
                    break
        knowledge = self._service(conversation).knowledge
        if knowledge is None:
            installation = self.commands.application.store.resolve_installation(
                ChannelKind.SLACK,
                self.commands.team_id,
            )
            access = MemoryAccess(
                scope=MemoryScope(
                    workspace_id=installation.tenant_id,
                    channel_id=conversation.channel_id,
                    product_id="trace",
                    work_id=run.run_id,
                    member_id=conversation.owner_id if conversation.private else "",
                    session_id=conversation.conversation_id if conversation.private else "",
                ),
                actor_id=conversation.owner_id if conversation.private else "trace-agent",
                private=conversation.private,
            )
            selection = SQLiteMemoryStore(self.store.database_path).select(
                access=access,
                query=query[:8000],
                run_id=run.run_id,
                now=now,
            )
        else:
            selection = knowledge.select_legacy_memory(
                run,
                query=query[:8000],
                now=now,
            )
        if selection is None or not selection.notes:
            return None
        return {
            "schema_version": "trace.current-memory-context.v1",
            "data": selection.model_dump(mode="json"),
        }

    def _pending_steering(self, tenant_id: str, run_id: str) -> JsonObject | None:
        for message in self.store.pending_for_run(tenant_id, run_id):
            command = message.text.split(" ", 1)[0]
            if (
                message.text.rstrip("?!. ") in _STATUS_TEXTS
                or command
                in {"검토", "review", "승인", "approve", "거절", "reject", "기억", "실행안"}
                or is_work_observation_command(message.text)
                or is_performance_command(message.text)
                or message.text
                in {
                    "도움말",
                    "help",
                    "종료",
                    "close",
                    "계속",
                    "resume",
                    "다시 시작",
                    "reopen",
                    "이대로 만들어줘",
                    "이대로 제작해줘",
                }
            ):
                continue
            conversation = self.store.conversation(message.conversation_id)
            if conversation is None:
                continue
            try:
                identity = self._authorize(conversation, message.user_id)
            except ValueError:
                continue
            return {
                "event_id": message.message_id,
                "actor_id": identity.member_id,
                "note": message.text,
            }
        return None

    def identity(self, user_id: str) -> ChannelIdentityBinding:
        installation = self.commands.application.store.resolve_installation(
            ChannelKind.SLACK, self.commands.team_id
        )
        if not re.fullmatch(r"[UW][A-Z0-9]+", user_id):
            raise ValueError("slack_user_invalid")
        identity = (
            self.commands.application.store.bind_workspace_member(installation, user_id)
            if self.workspace_mentions
            else self.commands.application.store.resolve_identity(
                installation.installation_id, user_id
            )
        )
        if (
            not installation.enabled
            or not identity.can_create_runs
            or identity.revoked_at is not None
            or (not self.workspace_mentions and user_id not in self.commands.allowed_user_ids)
        ):
            raise ValueError("slack_user_not_allowed")
        return identity

    def receive(  # noqa: C901,PLR0911 - explicit Slack admission variants.
        self, body: bytes, headers: dict[str, str], *, now: datetime
    ) -> JsonObject:
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
        subtype = event.get("subtype")
        if kind not in {"message", "app_mention"} or event.get("bot_id"):
            return {"ok": True}
        if subtype in {"message_changed", "message_deleted"}:
            self._receive_mutation(event, now=now)
            return {"ok": True}
        if subtype not in {None, "file_share"}:
            return {"ok": True}
        if subtype == "file_share" and not isinstance(event.get("files"), list):
            return {"ok": True}
        user_id = _string(event, "user")
        if user_id == self.bot_user_id:
            return {"ok": True}
        try:
            identity = self.identity(user_id)
        except ValueError:
            return {"ok": True}
        admitted = self._message(event, identity, now=now)
        if admitted is not None:
            conversation, message, knowledge = admitted
            self.store.admit(conversation, message, knowledge)
        return {"ok": True}

    def _message(  # noqa: C901,PLR0911,PLR0912 - explicit channel and DM admission boundaries.
        self, event: JsonObject, identity: ChannelIdentityBinding, *, now: datetime
    ) -> tuple[Conversation, Message, tuple[PendingKnowledgeIngress, ...]] | None:
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
            if not re.fullmatch(r"[CG][A-Z0-9]+", channel):
                return None
            if not self.workspace_mentions and channel not in self.channel_ids:
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
        source_text = text
        reopens = text in {"다시 시작", "reopen"}
        if current is not None and current.closed and not reopens:
            return None
        attachments = attachment_references(event)
        if not text:
            text = (
                "첨부한 작업 결과를 확인하고 다음에 필요한 일을 알려줘" if attachments else "도움말"
            )
        conversation = current or Conversation(
            conversation_id=conversation_id,
            tenant_id=f"slack-private-{scope}" if private else identity.tenant_id,
            channel_id=channel,
            thread_ts=thread,
            owner_id=identity.member_id if private else "",
            private=private,
        )
        message = Message(
            message_id=self._message_id(channel, ts),
            conversation_id=conversation_id,
            user_id=identity.external_user_id,
            text=text,
            reopens=reopens,
            attachments=attachments,
        )
        run = self._service(conversation).repository.get(
            conversation.tenant_id, conversation.current_run
        )
        if text in {"이대로 만들어줘", "이대로 제작해줘", "승인", "진행해줘", "approve"}:
            assent = self._production_approval(conversation, message, run, at_admission=True)
            if assent.action == "approve":
                message = message.model_copy(update={"reviewed_invocation_sha256": assent.digest})
        action = "input" if run is not None and not text.startswith("새 작업 ") else "create"
        run_id = (
            run.run_id
            if action == "input" and run is not None
            else "slack-talk-"
            + contract_sha256(
                {"message": message.message_id, "scope": conversation.conversation_id}
            )[:40]
        )
        files = _json_objects(event.get("files"))
        attachments = attachment_capabilities(
            SlackAttachmentContext(identity.tenant_id, message.message_id, files)
        )
        event_kind = (
            ConversationEventKind.MESSAGE_FINALIZED
            if source_text
            else ConversationEventKind.ATTACHMENT_RECEIVED
        )
        knowledge = build_slack_ingresses(
            SlackIngressRequest(
                conversation_id=conversation.conversation_id,
                message_id=message.message_id,
                run_id=run_id,
                action=action,
                text=source_text,
                revision=1,
                external_revision=ts,
                created_revision=ts,
                event_kind=event_kind,
                identity=identity,
                private=private,
                channel_id=conversation.channel_id,
                reply_to=self._message_id(channel, thread) if thread and thread != ts else None,
                attachments=attachments,
                observed_at=now,
            )
        )
        return conversation, message, knowledge

    def _receive_mutation(self, event: JsonObject, *, now: datetime) -> None:
        channel = _string(event, "channel")
        raw_previous = event.get("previous_message")
        if not isinstance(raw_previous, dict):
            return
        previous = _JSON.validate_python(raw_previous)
        original_ts = _string(previous, "ts", default=_string(event, "deleted_ts", default=""))
        if not _TIMESTAMP.fullmatch(original_ts):
            raise ValueError("slack_original_timestamp_invalid")
        admitted = self.store.admitted_message(self._message_id(channel, original_ts))
        if admitted is None:
            return
        prior = self.store.latest_knowledge_event(admitted.message_id)
        if prior is None:
            return
        previous_event, previous_binding = prior
        identity = self.identity(_string(previous, "user"))
        if previous_event.speaker_ref != identity.member_id:
            raise ValueError("slack_message_owner_mismatch")
        conversation = self.store.conversation(admitted.conversation_id)
        if conversation is None:
            raise ValueError("slack_conversation_missing")
        _ = self._authorize(conversation, identity.external_user_id)
        subtype = _string(event, "subtype")
        if subtype == "message_changed":
            changed = _json_object(event.get("message"))
            edited = _json_object(changed.get("edited"))
            external_revision = _string(edited, "ts", default=_string(event, "event_ts"))
            text = _string(changed, "text").replace(f"<@{self.bot_user_id}>", "").strip()
            event_kind = ConversationEventKind.MESSAGE_EDITED
        else:
            external_revision = _string(event, "event_ts")
            text = ""
            event_kind = ConversationEventKind.MESSAGE_DELETED
        if slack_revision(external_revision) <= slack_revision(previous_binding.source_version):
            raise ValueError("slack_message_revision_stale")
        knowledge = build_slack_ingress(
            SlackIngressRequest(
                conversation_id=conversation.conversation_id,
                message_id=admitted.message_id,
                run_id=self.store.knowledge_ingress.execution_run_for_message(admitted.message_id)
                or previous_binding.run_id,
                action="input",
                text=text,
                revision=previous_event.revision + 1,
                external_revision=external_revision,
                created_revision=original_ts,
                event_kind=event_kind,
                identity=identity,
                private=conversation.private,
                channel_id=conversation.channel_id,
                reply_to=previous_event.reply_to,
                attachments=(),
                observed_at=now,
            )
        )
        self.store.admit_mutation(knowledge)

    def _message_id(self, channel: str, timestamp: str) -> str:
        return "slack-message-" + contract_sha256(
            {"team": self.commands.team_id, "channel": channel, "ts": timestamp}
        )

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
            not self.workspace_mentions and conversation.channel_id not in self.channel_ids
        ) or identity.tenant_id != conversation.tenant_id:
            raise ValueError("slack_channel_removed")
        return identity

    def work_once(self, *, now: datetime) -> bool:  # noqa: C901 - ordered ingress, notification and cancellable run boundaries.
        if self._advance_background(now=now):
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
                if plan.run_id and (plan.action != "reply" or plan.learning_urgent):
                    self.store.knowledge_ingress.bind_execution(
                        message.message_id, plan.run_id, actor_id=identity.member_id
                    )
                if plan.action in {"create", "input", "resume", "approve", "revise"}:
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
                if plan.learning_urgent:
                    self._admit_urgent_learning(plan.run_id, now=now)
                self._admit_shared_turn(conversation, plan, now=now)
        except Exception as exc:  # noqa: BLE001 - never repeat an uncertain effect; no provider secrets.
            code = (
                exc.args[0]
                if isinstance(exc, ValueError)
                and len(exc.args) == 1
                and isinstance(exc.args[0], str)
                and exc.args[0] in _FAILURE_REPLIES
                else "unclassified"
            )
            _LOGGER.warning(
                "slack_event_failed message=%s action=%s code=%s",
                message.message_id,
                plan.action if plan else "planning",
                code,
            )
            self.store.finish(
                message,
                _FAILURE_REPLIES.get(
                    code,
                    " ".join(  # noqa: FLY002 - readable translated message.
                        (
                            "처리를 완료하지 못했습니다. 이 대화에 '상태'를 보내 확인하세요.",
                            "실행 확인 전에는 승인을 반복하지 말고 운영자에게 문의하세요.",
                        )
                    ),
                ),
                blocked=True,
            )
        _ = self._notify()
        return True

    def _advance_background(self, *, now: datetime) -> bool:
        if self.store.knowledge_ingress.dispatch_once():
            self._invalidate_learning_sources(now=now)
            return True
        return self._enqueue_learning_questions() or self._notify()

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

    def _plan(self, conversation: Conversation, message: Message) -> MessagePlan:  # noqa: C901,PLR0911,PLR0912 - freeze one of the supported conversation actions.
        text = message.text
        action, _, argument = text.partition(" ")
        if conversation.closed and not message.reopens:
            return MessagePlan(action="reply", reply="")
        if action == "실행안":
            return MessagePlan(action="delivery", run_id=conversation.current_run)
        if is_work_observation_command(text) or is_performance_command(text):
            return MessagePlan(action="observation", run_id=conversation.current_run)
        if action == "기억":
            return MessagePlan(action="memory", run_id=conversation.current_run)
        if text in {"종료", "close"}:
            return MessagePlan(action="close")
        if text in {"도움말", "help", "다시 시작", "reopen"}:
            return MessagePlan(action="reply", reply=_HELP)
        service = self._service(conversation)
        run = service.repository.get(conversation.tenant_id, conversation.current_run)
        if text.rstrip("?!. ") in _STATUS_TEXTS:
            return MessagePlan(
                action="reply", reply=self.summary(conversation, include_status=True)
            )
        if text.rstrip(".! ") in {"멈춰", "멈춰줘", "잠깐 멈춰줘", "중지", "pause", "stop"}:
            if run is None:
                return MessagePlan(action="reply", reply="아직 시작한 작업이 없습니다.")
            return MessagePlan(action="pause", run_id=run.run_id)
        if action in {"검토", "review"}:
            reply = self.commands.review_input(
                conversation.tenant_id, conversation.current_run, argument
            )
            return MessagePlan(action="reply", reply=reply)
        if text in {"거절", "reject", "취소", "취소해줘"}:
            pending = (
                None
                if run is None
                else service.pending_approval(conversation.tenant_id, run.run_id)
            )
            if (
                run is not None
                and run.state is AgentRunState.AWAITING_APPROVAL
                and pending is not None
            ):
                return MessagePlan(
                    action="reject", run_id=run.run_id, digest=contract_sha256(pending)
                )
            return MessagePlan(action="reply", reply="현재 승인 대기 중인 작업은 없습니다.")
        if text in {"이대로 만들어줘", "이대로 제작해줘", "승인", "진행해줘", "approve"}:
            return self._production_approval(conversation, message, run)
        if action in {"승인", "approve", "거절", "reject"} and (
            argument == "해시" or re.fullmatch(r"[a-fA-F0-9]{1,128}", argument)
        ):
            if run is None or not _HASH.fullmatch(argument):
                return MessagePlan(
                    action="reply",
                    reply=(
                        "'해시'라는 단어를 입력할 필요는 없어요. 위 실행 내용을 확인하고 "
                        "'승인' 또는 '거절'이라고 답해 주세요. 상세 내용은 '검토 1'로 볼 수 있어요."
                    ),
                )
            return MessagePlan(
                action="approve" if action in {"승인", "approve"} else "reject",
                run_id=run.run_id,
                digest=argument,
            )
        if text in {"계속", "resume"}:
            if run is not None and run.state is AgentRunState.AWAITING_INPUT:
                return MessagePlan(action="revise", run_id=run.run_id)
            return MessagePlan(action="resume", run_id=conversation.current_run)
        if (
            run is not None
            and not conversation.private
            and (
                learning_question_answer(text) is not None
                or is_ambiguous_learning_affirmation(text)
            )
        ):
            return MessagePlan(action="learning_answer", run_id=run.run_id)
        context = self.store.transcript(conversation.conversation_id)
        context["current_attachments"] = [a.model_dump(mode="json") for a in message.attachments]
        context["attachment_verification"] = "reference_only_not_visually_inspected"
        context["privacy"] = "private_dm" if conversation.private else "shared_thread"
        if (
            run is not None
            and run.state
            in {
                AgentRunState.COMPLETED,
                AgentRunState.STOPPED,
                AgentRunState.AWAITING_INPUT,
                AgentRunState.AWAITING_APPROVAL,
                AgentRunState.AWAITING_TOOL,
            }
            and not text.startswith("새 작업 ")
        ):
            return MessagePlan(action="revise", run_id=run.run_id)
        if text.startswith("새 작업 "):
            text = text.removeprefix("새 작업 ").strip() or text
        correction_signal = not conversation.private and has_learning_correction_signal(text)
        if run is not None and run.state not in {
            AgentRunState.COMPLETED,
            AgentRunState.STOPPED,
            AgentRunState.FAILED,
        }:
            return MessagePlan(
                action="reply",
                run_id=run.run_id if correction_signal else "",
                reply=self.summary(conversation),
                learning_urgent=correction_signal,
            )
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

    def _production_approval(  # noqa: PLR0911 - each proposal/identity/delivery check fails closed.
        self,
        conversation: Conversation,
        message: Message,
        run: AgentRun | None,
        *,
        at_admission: bool = False,
    ) -> MessagePlan:
        fallback = MessagePlan(
            action="reply",
            reply="먼저 현재 제작안의 '검토 1'부터 모든 페이지를 확인해 주세요.",
        )
        if run is None or run.state is not AgentRunState.AWAITING_APPROVAL:
            return MessagePlan(action="reply", reply="현재 승인 대기 중인 작업은 없습니다.")
        if conversation.private:
            return fallback
        records = self._service(conversation).repository.records(conversation.tenant_id, run.run_id)
        invocation = self._service(conversation).pending_approval(
            conversation.tenant_id, run.run_id
        )
        if invocation is None:
            return fallback
        if not at_admission and message.reviewed_invocation_sha256 != contract_sha256(invocation):
            return MessagePlan(
                action="reply",
                reply=self.summary(conversation),
            )
        snapshots = [
            CapabilitySnapshot.model_validate(r.payload)
            for r in records
            if r.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
            and r.payload_sha256 == invocation.capability_snapshot_sha256
        ]
        descriptors = [
            d
            for snapshot in snapshots
            for d in snapshot.descriptors
            if contract_sha256(d) == invocation.descriptor_sha256
        ]
        if len(descriptors) != 1:
            return fallback
        production_phrase = message.text in {"이대로 만들어줘", "이대로 제작해줘"}
        if production_phrase and (
            descriptors[0].effect_class is not EffectClass.LOCAL_ARTIFACT
            or descriptors[0].capability_id
            not in {
                "creative.image.generate",
                "creative.image.edit",
                "creative.image.localize",
                "creative.trace_post",
            }
        ):
            return fallback
        pages = self.commands.review_pages(conversation.tenant_id, run.run_id)
        with self.store.connect() as db:
            rows = db.execute(
                """SELECT result FROM slack_message_jobs
                WHERE conversation_id=? AND state='done' AND notification_state='delivered'
                AND json_extract(message_json,'$.user_id')=? ORDER BY rowid DESC LIMIT 100""",
                (conversation.conversation_id, message.user_id),
            ).fetchall()
        delivered = TypeAdapter(list[tuple[str]]).validate_python(rows)
        shown = proposal_text(invocation, descriptors[0])
        marker = f"승인 {contract_sha256(invocation)}"
        complete_summary = (
            len(shown) > 0
            and not shown.startswith("실행할 내용이 길어")
            and any(shown in result and marker in result for (result,) in delivered)
        )
        if not complete_summary and any((page,) not in delivered for page in pages):
            return fallback
        return MessagePlan(action="approve", run_id=run.run_id, digest=contract_sha256(invocation))

    def _execute(  # noqa: C901,PLR0911,PLR0912 - one canonical mutation per frozen action.
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
        if plan.action in {"approve", "reject"}:
            current = self.store.conversation(conversation.conversation_id)
            if current is None or current.current_run != plan.run_id:
                raise ValueError("slack_production_target_changed")
            if not current_approval_source(self.store, conversation, message, identity):
                raise ValueError("slack_approval_source_changed")
        if plan.action == "delivery":
            return delivery_command(
                self.store.database_path, conversation, message, identity, now=now
            )
        if plan.action == "observation":
            if conversation.current_run != plan.run_id:
                return "기록 대상 업무가 바뀌었습니다. 원래 업무에서 작업 기록을 다시 요청하세요."
            if is_performance_command(message.text):
                return performance_command(service, conversation, message, identity, now=now)
            return work_observation_command(service, conversation, message, identity, now=now)
        if plan.action == "memory":
            return memory_command(
                self.store.database_path, conversation, message, identity, now=now
            )
        if plan.action == "close":
            self.store.update_conversation(conversation.model_copy(update={"closed": True}))
            return "이 대화의 자동 응답을 종료했습니다. '다시 시작'을 보내면 재개합니다."
        if plan.action == "reply":
            self.store.update_conversation(conversation)
            return plan.reply
        if plan.action == "learning_answer":
            return self._answer_learning_question(conversation, message, plan, now=now)
        conversation = conversation.model_copy(update={"current_run": plan.run_id})
        self.store.update_conversation(conversation)
        if message.attachments and not conversation.private:
            bind_files(
                self.store.database_path,
                conversation.tenant_id,
                plan.run_id,
                conversation.channel_id,
                tuple(a.file_id for a in message.attachments),
            )
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
        elif plan.action in {"revise", "pause"}:
            continued = continue_work(
                service,
                conversation.tenant_id,
                plan.run_id,
                event_id=message.message_id,
                actor_id=identity.member_id,
                note=message.text,
                action="pause" if plan.action == "pause" else "revise",
                now=now,
                inputs={
                    "attachments": [a.model_dump(mode="json") for a in message.attachments],
                    "verification": "reference_only_not_visually_inspected",
                },
            )
            if continued.state is AgentRunState.AWAITING_TOOL:
                return (
                    "요청을 현재 업무에 기록했습니다. "
                    "진행 중인 도구의 결과를 확인한 뒤 반영합니다. "
                    "이미 시작된 작업이 취소됐다는 뜻은 아닙니다."
                )
            if plan.action == "pause":
                return (
                    "다음 작업을 멈췄습니다. 이미 실행된 결과는 유지됩니다. "
                    "수정 요청이나 사람 작업 결과를 보내면 이어갑니다."
                )
        elif plan.action == "input":
            run = service.repository.get(conversation.tenant_id, plan.run_id)
            if run is None or run.revision != plan.revision or plan.evidence is None:
                raise ValueError("slack_input_revision_changed")
            _ = service.submit_input(conversation.tenant_id, plan.run_id, plan.evidence, now=now)
        elif plan.action in {"approve", "reject"}:
            if not identity.can_approve or conversation.private:
                raise ValueError("slack_approval_not_allowed")
            if (
                message.text in {"이대로 만들어줘", "이대로 제작해줘"}
                and conversation.current_run != plan.run_id
            ):
                raise ValueError("slack_production_target_changed")
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
        if plan.action in {"create", "revise", "input"}:
            approve_requested_creation(
                service,
                self.store,
                conversation,
                message,
                self._authorize(conversation, message.user_id),
                now=now,
            )
        return self.summary(conversation)

    def _answer_learning_question(
        self,
        conversation: Conversation,
        message: Message,
        plan: MessagePlan,
        *,
        now: datetime,
    ) -> str:
        """Resolve an explicit source-thread question without using ToolApproval."""
        resolved = self._learning_question_answer_context(conversation, message, plan)
        if resolved is None:
            return "이 스레드에는 답변할 학습 질문이 없습니다."
        source, context, pending = resolved.source, resolved.context, resolved.pending
        parsed = learning_question_answer(message.text)
        if parsed is None:
            if not pending:
                return "이 스레드에는 답변할 학습 질문이 없습니다."
            if len(pending) != 1:
                choices = ", ".join(question.question_id for question in pending)
                return f"답변할 질문 ID를 지정해주세요: {choices}"
            question_id = pending[0].question_id
            answer_text = message.text
        else:
            question_id = parsed.question_id
            answer_text = parsed.answer
        if not any(question.question_id == question_id for question in pending):
            return "지정한 질문은 이 스레드에서 더 이상 답변할 수 없습니다."
        capabilities = tuple(
            grant.capability
            for grant in source.binding.actor.grants
            if grant.scope == source.event.scope and grant.policy_epoch == context.capability_epoch
        )
        try:
            _ = resolved.host.answer_question(
                TrustedQuestionAnswer(
                    question_id=question_id,
                    authenticated_event=AuthenticatedEvent(
                        event_id=source.event.message_id,
                        actor_ref=source.binding.actor.actor_id,
                        workspace_id=source.binding.actor.workspace_id,
                        scope=source.event.scope,
                        provenance=Provenance.HUMAN_DIRECT,
                        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
                        capabilities=capabilities,
                        policy_epoch=context.capability_epoch,
                        occurred_at=source.event.edited_at or source.event.created_at,
                    ),
                    answer=answer_text,
                    explicitly_adopts=False,
                    answered_at=now,
                ),
                context,
            )
        except QuestionError:
            return "지정한 질문은 현재 출처나 권한으로 답변할 수 없습니다."
        return f"{question_id}에 대한 답변을 기록했습니다."

    def _learning_question_answer_context(
        self, conversation: Conversation, message: Message, plan: MessagePlan
    ) -> _LearningQuestionAnswerContext | None:
        knowledge = self._service(conversation).knowledge
        if knowledge is None or conversation.private:
            return None
        source = self.store.knowledge_ingress.source_for_run(plan.run_id)
        if source is None:
            return None
        try:
            context = knowledge.resolve_question_answer(
                plan.run_id, f"{message.message_id}.learning-question-answer"
            )
        except AccessDeniedError, ValueError:
            return None
        return _LearningQuestionAnswerContext(
            source=source,
            context=context,
            pending=knowledge.host.questions.pending_for_conversation(
                conversation.conversation_id, context
            ),
            host=knowledge.host,
        )

    def _admit_urgent_learning(self, run_id: str, *, now: datetime) -> None:
        """Mark one summary-only correction source urgent without deciding its outcome."""
        if not run_id:
            return
        knowledge = self.commands.application.service.knowledge
        if knowledge is None:
            return
        source = self.store.knowledge_ingress.source_for_run(run_id)
        if source is None:
            return
        if knowledge.learning is None:
            return
        _ = knowledge.learning.admit_turn(
            source.binding,
            source.event,
            source.receipt,
            at=now,
            urgent=True,
        )

    def _admit_shared_turn(
        self,
        conversation: Conversation,
        plan: MessagePlan,
        *,
        now: datetime,
    ) -> None:
        """Count a completed shared conversational turn through its stored source receipt."""
        if conversation.private or plan.action not in {"create", "input", "resume", "revise"}:
            return
        knowledge = self.commands.application.service.knowledge
        if knowledge is None or knowledge.learning is None:
            return
        source = self.store.knowledge_ingress.source_for_run(plan.run_id)
        if source is None:
            return
        _ = knowledge.learning.admit_turn(
            source.binding,
            source.event,
            source.receipt,
            at=now,
        )

    def _invalidate_learning_sources(self, *, now: datetime) -> None:
        """Invalidate superseded shared learning sources after their canonical acknowledgement."""
        for conversation in self.store.conversations():
            if conversation.private or not conversation.current_run:
                continue
            knowledge = self._service(conversation).knowledge
            if knowledge is None or knowledge.learning is None:
                continue
            source = self.store.knowledge_ingress.source_for_run(conversation.current_run)
            if source is None or source.event.event_kind not in {
                ConversationEventKind.MESSAGE_EDITED,
                ConversationEventKind.MESSAGE_DELETED,
            }:
                continue
            _ = knowledge.learning.invalidate_source(
                source.binding.actor,
                source_id=source.receipt.source_id,
                source_revision_id=source.receipt.source_revision_id,
            )
            if source.event.event_kind is ConversationEventKind.MESSAGE_EDITED:
                _ = knowledge.learning.admit_turn(
                    source.binding,
                    source.event,
                    source.receipt,
                    at=now,
                    urgent=True,
                )

    def enqueue_run_update(self, tenant_id: str, run_id: str, *, event_id: str) -> bool:
        """Queue a canonical completion projection; delivery rechecks Slack membership."""
        conversation = self.store.conversation_for_run(tenant_id, run_id)
        if conversation is None or conversation.closed:
            return False
        service = self._service(conversation)
        with service.execution_lock:
            run = service.repository.get(tenant_id, run_id)
            if run is None:
                return False
            return self.store.enqueue_run_notification(
                tenant_id,
                run_id,
                event_id=event_id,
                result=self.summary(conversation)[:12000],
            )

    def summary(self, conversation: Conversation, *, include_status: bool = False) -> str:
        service = self._service(conversation)
        run = service.repository.get(conversation.tenant_id, conversation.current_run)
        if run is None:
            return "아직 시작한 작업이 없습니다. 요청을 텍스트로 보내주세요."
        records = service.repository.records(conversation.tenant_id, run.run_id)
        if run.state is AgentRunState.AWAITING_APPROVAL:
            invocation = service.pending_approval(conversation.tenant_id, run.run_id)
            if invocation is None:
                return "승인할 작업 기록을 확인하지 못했습니다. 실행하지 않았습니다."
            descriptor = next(
                d
                for r in records
                if r.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
                and r.payload_sha256 == invocation.capability_snapshot_sha256
                for d in CapabilitySnapshot.model_validate(r.payload).descriptors
                if contract_sha256(d) == invocation.descriptor_sha256
            )
            latest = next(
                (r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None
            )
            decision = None if latest is None else latest.payload.get("decision")
            answer = (
                str(decision.get("reasoning_summary", "")) + "\n\n"
                if isinstance(decision, dict) and decision.get("action") != "invoke_tool"
                else ""
            )
            return (
                answer
                + proposal_text(invocation, descriptor)
                + "\n\n이 내용으로 진행할까요? '승인' 또는 '진행해줘'라고 답해 주세요.\n"
                + "상세 기록은 '검토 1'로 확인할 수 있습니다.\n"
                + f"승인 {contract_sha256(invocation)}\n"
                + "진행하지 않으려면 '거절'이라고 답해 주세요."
            )
        latest = next((r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None)
        decision = None if latest is None else latest.payload.get("decision")
        answer = str(decision.get("reasoning_summary", "")) if isinstance(decision, dict) else ""
        if not include_status and run.state not in {
            AgentRunState.COMPLETED,
            AgentRunState.AWAITING_INPUT,
        }:
            # A tool-selection rationale is not a finished conversational answer.
            # Preserve canonical reasoning for explicit status/diagnostic reads.
            answer = {
                AgentRunState.AWAITING_TOOL: "요청한 도구의 결과를 기다리고 있습니다.",
                AgentRunState.AWAITING_RECONCILIATION: (
                    "실행 결과를 확인해야 합니다. 같은 작업을 다시 실행하지 않았습니다."
                ),
                AgentRunState.BLOCKED: "작업이 막혀 완료하지 못했습니다. 상태 확인이 필요합니다.",
                AgentRunState.STOPPED: "작업을 멈췄습니다. 이미 실행된 결과는 유지됩니다.",
                AgentRunState.FAILED: "오류로 작업을 완료하지 못했습니다. 상태 확인이 필요합니다.",
            }.get(run.state, "아직 작업이 완료되지 않았습니다.")
        verified_issues = "\n".join(
            line
            for line in issue_results(records).splitlines()
            if line.rsplit(" ", 1)[-1] not in answer
        )
        result = "\n\n".join(part for part in (answer, verified_issues) if part)
        if include_status:
            result += f"\n\n상태: {run.state.value}\n실행: {run.run_id}"
        return result

    def _payload(self, conversation: Conversation, text: str) -> JsonObject:
        blocks: list[JsonValue] = [
            {"type": "section", "text": {"type": "plain_text", "text": text[i : i + 2500]}}
            for i in range(0, len(text), 2500)
        ]
        if self.commands.public_links and not conversation.private and conversation.current_run:
            origin = self.commands.application.result_base_url.rstrip("/")
            result_url = f"{origin}/runs/{quote(conversation.current_run, safe='')}"
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"<{result_url}|업무·산출물 보기>",
                        }
                    ],
                }
            )
        payload: JsonObject = {
            "channel": conversation.channel_id,
            "text": text,
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": blocks,
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

    def _enqueue_learning_questions(self) -> bool:
        """Project only source-bound pending questions into the durable Slack outbox."""
        queued = False
        for conversation in self.store.conversations():
            if conversation.private or not conversation.current_run:
                continue
            knowledge = self._service(conversation).knowledge
            if knowledge is None:
                continue
            source = self.store.knowledge_ingress.source_for_run(conversation.current_run)
            if source is None:
                continue
            try:
                context = knowledge.resolve_question_answer(
                    conversation.current_run,
                    f"{source.binding.binding_id}.learning-question-notification",
                )
            except AccessDeniedError, ValueError:
                continue
            for question in knowledge.host.questions.pending_for_conversation(
                conversation.conversation_id,
                context,
            ):
                queued = self.store.enqueue_learning_question(question) or queued
        return queued

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


def _json_object(value: JsonValue) -> JsonObject:
    return _JSON.validate_python(value)


def _json_objects(value: JsonValue) -> tuple[JsonObject, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_JSON.validate_python(item) for item in value)


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
    installation = commands.application.store.resolve_installation(
        ChannelKind.SLACK, commands.team_id
    )
    token = env.get("TRACE_MARKETING_SLACK_BOT_TOKEN", "")
    if token and env.get("TRACE_MARKETING_SLACK_IMAGE_REVIEW") == "1":
        _ = connect_slack_creative(
            commands.application.service,
            tenant_id=installation.tenant_id,
            team_id=commands.team_id,
            token=token,
            now=datetime.now(UTC),
        )
    return SlackEvents(
        commands,
        bot_user_id,
        channels,
        allow_dm=env.get("TRACE_MARKETING_SLACK_ALLOW_DM", "1") == "1",
        workspace_mentions=True,
        image_delivery=SlackImageDelivery(
            commands.application.store.database_path.parent / "images",
            commands.application.store.database_path,
            env.get("TRACE_MARKETING_SLACK_BOT_TOKEN", ""),
        )
        if env.get("TRACE_MARKETING_SLACK_BOT_TOKEN")
        else None,
    )
