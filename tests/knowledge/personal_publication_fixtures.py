from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    DependencyState,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    IngestEnvelope,
    InstructionAuthority,
    MemoryDocument,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
    MemoryOrigin,
    MemoryRevision,
    MemoryStatus,
    MessageEventRef,
    Provenance,
    ScopeGrant,
    ScopeKind,
    UsageRole,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.scope_contracts import channel_member_scope
from ads_booster.knowledge.tool_contracts import MemoryRevisionPayload, TrustedInvocationContext

if TYPE_CHECKING:
    from ads_booster.knowledge.source_contracts import IngestReceipt

NOW: Final = datetime(2026, 9, 9, 0, tzinfo=UTC)


def personal_actor(member: str = "U1") -> ActorContext:
    channel = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="personal.qa", channel_id="CA")
    personal = AccessScope(
        kind=ScopeKind.CHANNEL_MEMBER,
        workspace_id=channel.workspace_id,
        channel_id=channel.channel_id,
        member_id=member,
    )
    return ActorContext(
        actor_id=f"actor.{member}",
        workspace_id=channel.workspace_id,
        member_id=member,
        session_id=f"session.{member}",
        conversation_scope=channel,
        grants=tuple(
            ScopeGrant(
                grant_id=f"{member}.{index}.{capability.value}",
                capability=capability,
                workspace_id=channel.workspace_id,
                scope=scope,
                policy_epoch=1,
                effective_at=NOW,
            )
            for index, scope in enumerate((channel, personal))
            for capability in (GrantCapability.READ, GrantCapability.WRITE)
        ),
        policy_epoch=1,
        authenticated_at=NOW,
    )


def ingest_personal_event(
    repository: SqliteKnowledgeRepository,
    author: ActorContext,
    *,
    message_id: str = "message.preference",
    role: ConversationRole = ConversationRole.USER,
) -> tuple[ConversationEvent, IngestReceipt]:
    repository.register_actor(author, MembershipRole.ADMIN)
    event = ConversationEvent(
        conversation_id="conversation.preferences",
        message_id=message_id,
        revision=1,
        sequence=1,
        role=role,
        speaker_ref=author.actor_id,
        text="I prefer short Korean replies.",
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=author.conversation_scope,
        created_at=NOW,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=f"delivery.{message_id}",
        event_kind=event.event_kind,
        request_text=event.text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id,
            message_ref=event.message_id,
            revision=event.revision,
        ),
        timestamp=NOW,
    )
    result = KnowledgeIngestion(repository).ingest(author, event, envelope)
    return event, result.unit_receipts[0].receipt


def user_payload(owner: ActorContext, event: ConversationEvent) -> MemoryRevisionPayload:
    target = channel_member_scope(owner)
    assert target is not None
    document_id = f"memory.user.{owner.member_id}"
    revision_id = f"{document_id}.r1"
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=owner.workspace_id,
        kind=MemoryKind.USER,
        timezone="UTC",
        head_revision_id=revision_id,
        scope=target,
    )
    is_user = event.role is ConversationRole.USER
    reference = EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id=event.message_id,
        revision_id=str(event.revision),
        quote_sha256=sha256(event.text.encode()).hexdigest(),
        scope=event.scope,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER
        if is_user
        else InstructionAuthority.DATA,
        provenance=Provenance.HUMAN_DIRECT if is_user else Provenance.AGENT_DERIVED,
    )
    entry = MemoryEntry(
        entry_id=f"entry.{document_id}",
        document_id=document_id,
        document_kind=MemoryKind.USER,
        text="Prefer short Korean replies.",
        kind=MemoryEntryKind.FACT,
        status=MemoryStatus.ACTIVE,
        dependency_state=DependencyState.CURRENT,
        origin=MemoryOrigin.DIRECT,
        usage_role=UsageRole.REFERENCE,
        scope=target,
        source_refs=(reference,),
        admission_reason="Owner expressed a response preference.",
    )
    body = f"# USER\n{entry.text}\n"
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body.encode()).hexdigest(),
        entry_ids=(entry.entry_id,),
        created_at=NOW,
    )
    operation = MemoryOperation(
        operation_id="operation.personal",
        kind=MemoryOperationKind.ADD,
        document_id=document_id,
        entry_id=entry.entry_id,
        expected_revision_id="none",
        reason="Record a personal response preference.",
        evidence_refs=(event.message_id,),
    )
    return MemoryRevisionPayload(
        operation=operation, document=document, revision=revision, entries=(entry,), body=body
    )


def personal_context(actor: ActorContext) -> TrustedInvocationContext:
    return TrustedInvocationContext(
        invocation_id="invocation.personal",
        actor=actor,
        run_binding_id="binding.personal",
        run_id="run.personal",
        capability_epoch=actor.policy_epoch,
        invoked_at=NOW,
    )
