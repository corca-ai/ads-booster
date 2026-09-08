"""Trusted Slack identity projection into canonical knowledge ingress contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AttachmentCapability,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    GrantCapability,
    IngestEnvelope,
    MessageEventRef,
    ScopeGrant,
    ScopeKind,
)
from ads_booster.marketing.agent_service.knowledge_ingress import (
    PendingKnowledgeIngress,
    TrustedRunBinding,
)
from ads_booster.marketing.channels.contracts import ChannelIdentityBinding


@dataclass(frozen=True, slots=True)
class SlackIngressRequest:
    conversation_id: str
    message_id: str
    run_id: str
    action: str
    text: str
    revision: int
    external_revision: str
    created_revision: str
    event_kind: ConversationEventKind
    identity: ChannelIdentityBinding
    private: bool
    reply_to: str | None
    attachments: tuple[AttachmentCapability, ...]
    observed_at: datetime


def build_slack_ingress(request: SlackIngressRequest) -> PendingKnowledgeIngress:
    revision = request.revision
    scope = AccessScope(
        kind=ScopeKind.MEMBER if request.private else ScopeKind.WORKSPACE,
        workspace_id=request.identity.tenant_id,
        member_id=request.identity.member_id if request.private else None,
        session_id=request.conversation_id if request.private else None,
    )
    actor = ActorContext(
        actor_id=request.identity.member_id,
        workspace_id=request.identity.tenant_id,
        member_id=request.identity.member_id,
        session_id=request.conversation_id,
        conversation_scope=scope,
        grants=tuple(
            ScopeGrant(
                grant_id="slack-ingress-grant-"
                + contract_sha256(
                    {
                        "binding": request.identity.binding_id,
                        "scope": grant_scope.model_dump(mode="json"),
                        "epoch": 1,
                        "capability": capability.value,
                    }
                )[:40],
                capability=capability,
                workspace_id=request.identity.tenant_id,
                scope=grant_scope,
                policy_epoch=1,
                effective_at=request.identity.created_at,
            )
            for capability, grant_scope in (
                (
                    GrantCapability.READ,
                    AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=request.identity.tenant_id),
                ),
                *(((GrantCapability.READ, scope),) if request.private else ()),
                (GrantCapability.WRITE, scope),
            )
        ),
        policy_epoch=1,
        authenticated_at=request.observed_at,
    )
    delivery_key = contract_sha256(
        {
            "installation": request.identity.installation_id,
            "message": request.message_id,
            "revision": revision,
            "kind": request.event_kind,
        }
    )
    delivery_id = "slack-delivery-" + delivery_key[:40]
    event = ConversationEvent(
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        revision=revision,
        sequence=revision,
        role=ConversationRole.USER,
        speaker_ref=request.identity.member_id,
        created_at=slack_datetime(request.created_revision),
        edited_at=request.observed_at
        if request.event_kind is ConversationEventKind.MESSAGE_EDITED
        else None,
        reply_to=request.reply_to,
        text=request.text,
        event_kind=request.event_kind,
        scope=scope,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=delivery_id,
        event_kind=request.event_kind,
        request_text=request.text,
        message_event=MessageEventRef(
            conversation_ref=request.conversation_id,
            message_ref=request.message_id,
            revision=revision,
        ),
        attachments=request.attachments,
        timestamp=request.observed_at,
    )
    binding = TrustedRunBinding(
        binding_id="knowledge-binding-" + delivery_key[:40],
        run_id=request.run_id,
        request_id=delivery_id,
        source="slack",
        action="input" if request.action == "input" else "create",
        source_version=request.external_revision,
        actor=actor,
        bound_at=request.observed_at,
    )
    return PendingKnowledgeIngress(binding=binding, event=event, envelope=envelope)


def build_slack_ingresses(request: SlackIngressRequest) -> tuple[PendingKnowledgeIngress, ...]:
    return (build_slack_ingress(request),)


def slack_revision(value: str) -> int:
    seconds, _, fraction = value.partition(".")
    return int(seconds) * 1_000_000 + int((fraction + "000000")[:6])


def slack_datetime(value: str) -> datetime:
    return datetime.fromtimestamp(slack_revision(value) / 1_000_000, tz=UTC)


__all__ = [
    "SlackIngressRequest",
    "build_slack_ingress",
    "build_slack_ingresses",
    "slack_datetime",
    "slack_revision",
]
