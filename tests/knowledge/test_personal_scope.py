from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ads_booster.knowledge.change_validation import ChangeValidationError, require_scope_not_wider
from ads_booster.knowledge.contract_types import GrantCapability, MemoryKind, ScopeKind
from ads_booster.knowledge.errors import AccessDeniedError, ScopeIntersectionError
from ads_booster.knowledge.grant_policy import (
    authorize_read,
    authorize_write,
    intersect_lineage_scopes,
)
from ads_booster.knowledge.memory_contracts import MemoryDocument
from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext, ScopeGrant

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _personal(channel: str = "CA", member: str = "U1") -> AccessScope:
    return AccessScope.model_validate(
        {"kind": "channel_member", "workspace_id": "T1", "channel_id": channel, "member_id": member}
    )


def _actor(target: AccessScope, session_id: str = "thread1") -> ActorContext:
    return ActorContext(
        actor_id="U1",
        workspace_id="T1",
        member_id="U1",
        session_id=session_id,
        conversation_scope=AccessScope(kind=ScopeKind.CHANNEL, workspace_id="T1", channel_id="CA"),
        grants=tuple(
            ScopeGrant(
                grant_id=f"grant.{capability.value}",
                capability=capability,
                workspace_id="T1",
                scope=target,
                policy_epoch=1,
                effective_at=NOW,
            )
            for capability in (GrantCapability.READ, GrantCapability.WRITE)
        ),
        policy_epoch=1,
        authenticated_at=NOW,
    )


def test_personal_scope_grants_survive_thread_identity_change() -> None:
    target = _personal()
    for session in ("thread1", "thread2"):
        actor = _actor(target, session)
        assert authorize_read(actor=actor, target_scope=target, at=NOW).scope == target
        assert authorize_write(actor=actor, target_scope=target, at=NOW).scope == target
    assert target.session_id is None


@pytest.mark.parametrize(("channel", "member"), [("CA", "U2"), ("CB", "U1")])
def test_foreign_personal_scope_denied_even_with_matching_grant(channel: str, member: str) -> None:
    target = _personal(channel, member)
    actor = _actor(target)
    with pytest.raises(AccessDeniedError, match="personal_scope_actor_mismatch"):
        _ = authorize_read(actor=actor, target_scope=target, at=NOW)
    with pytest.raises(AccessDeniedError, match="personal_scope_actor_mismatch"):
        _ = authorize_write(actor=actor, target_scope=target, at=NOW)


def test_channel_lineage_narrows_only_to_same_channel_member() -> None:
    channel = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="T1", channel_id="CA")
    own = _personal()
    require_scope_not_wider(source=channel, target=own, target_id="preference")
    assert intersect_lineage_scopes((channel, own)) == own
    assert intersect_lineage_scopes((own, channel)) == own
    for source, target in (
        (own, channel),
        (own, _personal(member="U2")),
        (channel, _personal("CB")),
    ):
        with pytest.raises(ChangeValidationError, match="scope_expansion_forbidden"):
            require_scope_not_wider(source=source, target=target, target_id="preference")
    with pytest.raises(ScopeIntersectionError):
        _ = intersect_lineage_scopes((own, _personal(member="U2")))


def test_user_document_requires_personal_owner() -> None:
    document = MemoryDocument.model_validate(
        {
            "document_id": "user1",
            "workspace_id": "T1",
            "kind": "user",
            "timezone": "UTC",
            "head_revision_id": "revision1",
            "scope": _personal(),
        }
    )
    assert document.owned_scope == _personal()
    assert document.brand_id is None
    assert document.local_date is None
    with pytest.raises(ValidationError, match="user_memory_scope_required"):
        _ = MemoryDocument.model_validate(
            {
                **document.model_dump(),
                "scope": AccessScope(kind=ScopeKind.CHANNEL, workspace_id="T1", channel_id="CA"),
            }
        )
    with pytest.raises(ValidationError, match="memory_scope_private"):
        _ = MemoryDocument.model_validate({**document.model_dump(), "kind": MemoryKind.CORE})


def test_personal_scope_is_not_a_conversation_scope() -> None:
    actor = _actor(_personal())
    with pytest.raises(ValidationError, match="actor_personal_scope_not_conversation"):
        _ = ActorContext.model_validate({**actor.model_dump(), "conversation_scope": _personal()})
