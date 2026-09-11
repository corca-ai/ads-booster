"""Narrow creation delegation and readable review at the authenticated Slack boundary."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    AgentRunState,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.knowledge.contract_types import ConversationEventKind

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.contracts import ChannelIdentityBinding
    from ads_booster.channels.slack_conversations import (
        Conversation,
        Message,
        SlackConversationStore,
    )
    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor


_CREATION_TOOLS = frozenset(
    {
        "creative.image.generate",
        "creative.image.edit",
        "creative.image.localize",
        "creative.trace_post",
    }
)
_MAX_PROPOSAL_CHARS = 2000


def current_approval_source(
    store: SlackConversationStore,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
) -> bool:
    source = store.latest_knowledge_event(message.message_id)
    if source is None:
        return False
    event, binding = source
    return (
        event.event_kind is ConversationEventKind.MESSAGE_FINALIZED
        and event.text == message.text
        and event.conversation_id == conversation.conversation_id
        and binding.actor.member_id == identity.member_id
    )


def creation_delegation_allowed(descriptor: ToolDescriptor, invocation: ToolInvocation) -> bool:
    """Never expand local production intent into publication, spending or arbitrary tools."""
    return (
        descriptor.capability_id in _CREATION_TOOLS
        and descriptor.effect_class is EffectClass.LOCAL_ARTIFACT
    ) or (
        descriptor.capability_id == "github.issue.create"
        and descriptor.effect_class is EffectClass.EXTERNAL
        and invocation.input.get("repository") == "corca-ai/ads-booster"
    )


def approve_requested_creation(  # noqa: PLR0911,PLR0913 - explicit fail-closed channel checks.
    service: MarketingAgentService,
    store: SlackConversationStore,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
    *,
    now: datetime,
) -> None:
    """Bind a provider's semantic reading to this admitted event and one exact invocation.

    This is not a regex permission classifier. The provider interprets the complete
    user request; scope, identity, freshness and one-effect bounds remain host checks.
    Other adapters and messages cannot opt into this policy by supplying tool text.
    """
    run = service.repository.get(conversation.tenant_id, conversation.current_run)
    if conversation.private or run is None or run.state is not AgentRunState.AWAITING_APPROVAL:
        return
    if not identity.can_approve or identity.tenant_id != run.tenant_id:
        return
    if not current_approval_source(store, conversation, message, identity):
        return
    records = service.repository.records(run.tenant_id, run.run_id)
    if any(
        r.kind is AgentRecordKind.APPROVAL
        and r.payload.get("request_event_id") == message.message_id
        for r in records
    ):
        return
    pending = service.pending_approval(run.tenant_id, run.run_id)
    if pending is None:
        return
    reasoning = next((r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None)
    decision = None if reasoning is None else reasoning.payload.get("decision")
    if not isinstance(decision, dict) or decision.get("authorization_message") != message.text:
        return
    if decision.get("action") != "invoke_tool" or decision.get("tool_input") != pending.input:
        return
    descriptor = next(
        (
            d
            for r in records
            if r.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
            and r.payload_sha256 == pending.capability_snapshot_sha256
            for d in CapabilitySnapshot.model_validate(r.payload).descriptors
            if contract_sha256(d) == pending.descriptor_sha256
        ),
        None,
    )
    if (
        descriptor is None
        or decision.get("capability_id") != descriptor.capability_id
        or not creation_delegation_allowed(descriptor, pending)
    ):
        return
    _ = service.decide_approval(
        run.tenant_id,
        run.run_id,
        approver_id=identity.member_id,
        granted=True,
        expected_invocation_sha256=contract_sha256(pending),
        expires_at=now + timedelta(minutes=5),
        request_event_id=message.message_id,
        request_text_sha256=contract_sha256({"text": message.text}),
        now=now,
    )


def proposal_text(invocation: ToolInvocation, descriptor: ToolDescriptor) -> str:
    """Render all executable inputs; oversized proposals retain paginated exact review."""
    labels = {
        "creative.image.generate": "이미지 만들기",
        "creative.image.edit": "이미지 수정",
        "creative.image.localize": "이미지 현지화",
        "creative.trace_post": "Trace 게시물 제작",
        "github.issue.create": "GitHub 이슈 등록",
    }
    fields = {"prompt": "제작 내용", "repository": "저장소", "title": "제목", "body": "본문"}
    details = "\n".join(f"{fields.get(k, k)}: {v}" for k, v in invocation.input.items())
    if len(details) > _MAX_PROPOSAL_CHARS:
        return "실행할 내용이 길어 나누어 보여드릴게요. '검토 1'로 내용을 확인해 주세요."
    return f"{labels.get(descriptor.capability_id, descriptor.capability_id)}\n{details}"
