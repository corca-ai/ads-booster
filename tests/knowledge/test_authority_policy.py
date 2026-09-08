from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AuthenticatedEvent,
    AuthorityClass,
    AuthorityRef,
    DependencyState,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    InstructionAuthority,
    MemoryDocument,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    Provenance,
    ScopeKind,
    SoulSection,
    UsageRole,
)
from ads_booster.knowledge.errors import AuthorityViolationError
from ads_booster.knowledge.policy import MemoryAuthorityContext, require_memory_authority

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


def _scope() -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-a")


def _authority() -> AuthorityRef:
    return AuthorityRef(
        event_id="event.soul.1",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="member.a1",
        workspace_id="workspace.team-a",
        scope=_scope(),
        policy_epoch=7,
    )


def _actor() -> ActorContext:
    return ActorContext(
        actor_id="member.a1",
        workspace_id="workspace.team-a",
        member_id="member.a1",
        session_id="session.channel.1",
        conversation_scope=_scope(),
        policy_epoch=7,
        authenticated_at=NOW,
    )


def _entry() -> MemoryEntry:
    return MemoryEntry(
        entry_id="entry.soul.voice.1",
        document_id="memory.soul.a",
        document_kind=MemoryKind.SOUL,
        text="Use concrete, verifiable scenes.",
        kind=MemoryEntryKind.DECISION,
        status=MemoryStatus.ACTIVE,
        dependency_state=DependencyState.CURRENT,
        origin=MemoryOrigin.DIRECT,
        usage_role=UsageRole.CONSTRAINT,
        scope=_scope(),
        source_refs=(
            EvidenceRef(
                evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                evidence_id="event.soul.1",
                revision_id="source.soul.rev1",
                scope=_scope(),
                instruction_authority=InstructionAuthority.AUTHORIZED_USER,
                provenance=Provenance.HUMAN_DIRECT,
            ),
        ),
        authority_ref=_authority(),
        soul_section=SoulSection.VOICE,
        admission_reason="Explicit brand adoption.",
    )


def _document() -> MemoryDocument:
    return MemoryDocument(
        document_id="memory.soul.a",
        workspace_id="workspace.team-a",
        kind=MemoryKind.SOUL,
        brand_id="brand.a",
        timezone="Asia/Seoul",
        head_revision_id="memory.soul.a.rev1",
    )


def test_active_soul_authority_requires_voice_edit_capability() -> None:
    event = AuthenticatedEvent(
        event_id="event.soul.1",
        actor_ref="member.a1",
        workspace_id="workspace.team-a",
        scope=_scope(),
        provenance=Provenance.HUMAN_DIRECT,
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        policy_epoch=7,
        occurred_at=NOW,
    )
    context = MemoryAuthorityContext(document=_document(), actor=_actor(), events=(event,))

    with pytest.raises(AuthorityViolationError, match="soul_authority_required"):
        _ = require_memory_authority(entry=_entry(), context=context)

    authorized = event.model_copy(update={"capabilities": (GrantCapability.BRAND_VOICE_EDIT,)})
    authorized_context = MemoryAuthorityContext(
        document=_document(),
        actor=_actor(),
        events=(authorized,),
    )
    assert require_memory_authority(entry=_entry(), context=authorized_context) == authorized


def test_soul_authority_rejects_cross_workspace_event() -> None:
    event = AuthenticatedEvent(
        event_id="event.soul.1",
        actor_ref="member.a1",
        workspace_id="workspace.team-b",
        scope=AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-b"),
        provenance=Provenance.HUMAN_DIRECT,
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        capabilities=(GrantCapability.BRAND_VOICE_EDIT,),
        policy_epoch=7,
        occurred_at=NOW,
    )
    context = MemoryAuthorityContext(document=_document(), actor=_actor(), events=(event,))

    with pytest.raises(AuthorityViolationError, match="memory_authority_binding_mismatch"):
        _ = require_memory_authority(entry=_entry(), context=context)
