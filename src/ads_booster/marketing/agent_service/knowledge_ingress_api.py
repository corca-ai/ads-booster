"""Authenticated HTTP request projection into canonical knowledge ingress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass runtime annotation owner.

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
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
from ads_booster.marketing.agent_service.oauth import OAuthIdentity


@dataclass(frozen=True, slots=True)
class ApiIngressRequest:
    request_id: str
    run_id: str
    action: str
    text: str
    identity: OAuthIdentity
    revision: int
    occurred_at: datetime
    corrects_revision_ref: str | None = None


def build_api_ingress(request: ApiIngressRequest) -> PendingKnowledgeIngress:
    conversation_id = (
        "api-conversation-"
        + contract_sha256({"workspace": request.identity.tenant_id, "run": request.run_id})[:40]
    )
    message_id = (
        "api-message-"
        + contract_sha256(
            {
                "workspace": request.identity.tenant_id,
                "run": request.run_id,
                "request": request.request_id,
            }
        )[:40]
    )
    scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=request.identity.tenant_id)
    actor = ActorContext(
        actor_id=request.identity.principal_id,
        workspace_id=request.identity.tenant_id,
        member_id=request.identity.principal_id,
        session_id=conversation_id,
        conversation_scope=scope,
        grants=tuple(
            ScopeGrant(
                grant_id="api-ingress-grant-"
                + contract_sha256(
                    {
                        "workspace": request.identity.tenant_id,
                        "principal": request.identity.principal_id,
                        "scope": scope.model_dump(mode="json"),
                        "epoch": 1,
                        "capability": capability.value,
                    }
                )[:40],
                capability=capability,
                workspace_id=request.identity.tenant_id,
                scope=scope,
                policy_epoch=1,
                effective_at=request.occurred_at,
            )
            for capability in (GrantCapability.READ, GrantCapability.WRITE)
        ),
        policy_epoch=1,
        authenticated_at=request.occurred_at,
    )
    event = ConversationEvent(
        conversation_id=conversation_id,
        message_id=message_id,
        revision=request.revision,
        sequence=request.revision,
        role=ConversationRole.USER,
        speaker_ref=request.identity.principal_id,
        created_at=request.occurred_at,
        text=request.text,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=scope,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=request.request_id,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        request_text=request.text,
        message_event=MessageEventRef(
            conversation_ref=conversation_id,
            message_ref=message_id,
            revision=request.revision,
            corrects_revision_ref=request.corrects_revision_ref,
        ),
        timestamp=request.occurred_at,
    )
    binding = TrustedRunBinding(
        binding_id="knowledge-binding-"
        + contract_sha256(
            {
                "workspace": request.identity.tenant_id,
                "run": request.run_id,
                "request": request.request_id,
            }
        )[:40],
        run_id=request.run_id,
        request_id=request.request_id,
        source="api",
        action="input" if request.action == "input" else "create",
        source_version=str(request.revision),
        actor=actor,
        bound_at=request.occurred_at,
    )
    return PendingKnowledgeIngress(binding=binding, event=event, envelope=envelope)


__all__ = ["ApiIngressRequest", "build_api_ingress"]
