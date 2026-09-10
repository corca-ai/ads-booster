from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.knowledge_ingress_authority import KnowledgeIngressAuthority
from ads_booster.knowledge.batch_actor import load_batch_actor
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    GrantCapability,
    ScopeGrant,
    ScopeKind,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from tests.marketing.agent_service.test_http_api import NOW

if TYPE_CHECKING:
    from pathlib import Path


def channel_actor(channel: str, session: str = "thread-one") -> ActorContext:
    scope = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="team", channel_id=channel)
    return ActorContext(
        actor_id="alice",
        workspace_id="team",
        member_id="alice",
        session_id=session,
        conversation_scope=scope,
        grants=tuple(
            ScopeGrant(
                grant_id=f"{channel}-{capability.value}",
                capability=capability,
                workspace_id="team",
                scope=scope,
                policy_epoch=1,
                effective_at=NOW - timedelta(days=1),
            )
            for capability in (GrantCapability.READ, GrantCapability.WRITE)
        ),
        policy_epoch=1,
        authenticated_at=NOW,
    )


def test_restarted_batch_actor_keeps_bound_channel_grants(tmp_path: Path) -> None:
    actor = channel_actor("C1")
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    repository.register_actor(actor, MembershipRole.EDITOR)
    repository.register_actor(channel_actor("C2"), MembershipRole.EDITOR)

    restarted = SqliteKnowledgeRepository(tmp_path / "knowledge")
    restored = load_batch_actor(restarted, actor.conversation_scope, NOW, submitter=actor)

    assert restored.grants == actor.grants
    assert restored.member_id == "alice"
    assert restored.session_id == "thread-one"
    assert restored.conversation_scope.member_id is None
    assert restored.conversation_scope.session_id is None


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE members SET state='disabled'",
        "UPDATE sessions SET state='closed'",
        "UPDATE memberships SET role='reader'",
        "UPDATE workspaces SET policy_epoch=2",
        "DELETE FROM scope_grants WHERE capability='write'",
    ],
)
def test_restarted_batch_actor_rejects_revoked_authority(tmp_path: Path, mutation: str) -> None:
    actor = channel_actor("C1")
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    repository.register_actor(actor, MembershipRole.EDITOR)
    with repository.connection() as db:
        _ = db.execute(mutation)

    with pytest.raises(KnowledgePolicyError):
        _ = load_batch_actor(
            SqliteKnowledgeRepository(tmp_path / "knowledge"),
            actor.conversation_scope,
            NOW,
            submitter=actor,
        )


@pytest.mark.parametrize(("fresh", "epoch"), [(False, 1), (True, 1), (True, 2)])
def test_channel_ingress_does_not_recreate_deleted_grant(
    tmp_path: Path,
    fresh: bool,
    epoch: int,
) -> None:
    actor = channel_actor("C1")
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    repository.register_actor(actor, MembershipRole.EDITOR)
    authority = KnowledgeIngressAuthority(repository)
    bound = authority.bind_actor(actor, fresh=True)
    with repository.connection() as db:
        _ = db.execute(
            "DELETE FROM scope_grants WHERE workspace_id=? AND grant_id=?",
            (bound.workspace_id, bound.grants[1].grant_id),
        )
        _ = db.execute("UPDATE workspaces SET policy_epoch=?", (epoch,))

    with pytest.raises(KnowledgePolicyError):
        _ = authority.bind_actor(bound, fresh=fresh)
