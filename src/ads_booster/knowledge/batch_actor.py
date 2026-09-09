from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import GrantCapability, ScopeKind
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.scope_contracts import ActorContext, ScopeGrant

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import AccessScope

_IDENTITY: TypeAdapter[tuple[str, int] | None] = TypeAdapter(tuple[str, int] | None)
_GRANTS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


def load_batch_actor(
    repository: SqliteKnowledgeRepository,
    scope: AccessScope,
    now: datetime,
) -> ActorContext:
    if scope.member_id is None or scope.session_id is None:
        raise KnowledgePolicyError(code="knowledge_batch_scope_identity_missing")
    with repository.connection() as connection:
        _ = connection.execute("BEGIN")
        identity = _IDENTITY.validate_python(
            connection.execute(
                """SELECT member.actor_id,workspace.policy_epoch FROM members AS member
            JOIN workspaces AS workspace USING(workspace_id)
            JOIN memberships AS membership USING(workspace_id,member_id)
            JOIN sessions AS session USING(workspace_id,member_id)
            WHERE member.workspace_id=? AND member.member_id=? AND session.session_id=?
                AND workspace.state='active' AND member.state='active'
                AND membership.state='active' AND session.state='active'""",
                (scope.workspace_id, scope.member_id, scope.session_id),
            ).fetchone()
        )
        if identity is None:
            raise KnowledgePolicyError(code="knowledge_batch_actor_unavailable")
        rows = _GRANTS.validate_python(
            connection.execute(
                """SELECT grant_json FROM scope_grants
            WHERE workspace_id=? AND member_id=? AND policy_epoch=?
            ORDER BY grant_id""",
                (scope.workspace_id, scope.member_id, identity[1]),
            ).fetchall()
        )
    actor = ActorContext(
        actor_id=identity[0],
        workspace_id=scope.workspace_id,
        member_id=scope.member_id,
        session_id=scope.session_id,
        conversation_scope=scope,
        grants=tuple(
            grant
            for (encoded,) in rows
            for grant in (ScopeGrant.model_validate_json(encoded),)
            if grant.scope == scope
            or (
                grant.scope.kind is ScopeKind.WORKSPACE and grant.capability is GrantCapability.READ
            )
        ),
        policy_epoch=identity[1],
        authenticated_at=now,
    )
    _ = authorize_read(actor=actor, target_scope=scope, at=now)
    _ = authorize_write(actor=actor, target_scope=scope, at=now)
    return actor


def load_partition_actor(
    repository: SqliteKnowledgeRepository,
    bound: ActorContext,
    now: datetime,
) -> ActorContext:
    """Reload the exact member and session retained by a learning partition."""
    with repository.connection() as connection:
        _ = connection.execute("BEGIN")
        identity = _IDENTITY.validate_python(
            connection.execute(
                """SELECT member.actor_id,workspace.policy_epoch FROM members AS member
                JOIN workspaces AS workspace USING(workspace_id)
                JOIN memberships AS membership USING(workspace_id,member_id)
                JOIN sessions AS session USING(workspace_id,member_id)
                WHERE member.workspace_id=? AND member.member_id=? AND session.session_id=?
                    AND workspace.state='active' AND member.state='active'
                    AND membership.state='active' AND session.state='active'""",
                (bound.workspace_id, bound.member_id, bound.session_id),
            ).fetchone()
        )
        if identity is None or identity != (bound.actor_id, bound.policy_epoch):
            raise KnowledgePolicyError(code="knowledge_batch_actor_unavailable")
        rows = _GRANTS.validate_python(
            connection.execute(
                """SELECT grant_json FROM scope_grants
                WHERE workspace_id=? AND member_id=? AND policy_epoch=?
                ORDER BY grant_id""",
                (bound.workspace_id, bound.member_id, bound.policy_epoch),
            ).fetchall()
        )
    bound_grant_ids = frozenset(grant.grant_id for grant in bound.grants)
    actor = bound.model_copy(
        update={
            "grants": tuple(
                grant
                for (encoded,) in rows
                for grant in (ScopeGrant.model_validate_json(encoded),)
                if grant.grant_id in bound_grant_ids
            ),
            "authenticated_at": now,
        }
    )
    _ = authorize_read(actor=actor, target_scope=actor.conversation_scope, at=now)
    _ = authorize_write(actor=actor, target_scope=actor.conversation_scope, at=now)
    return actor


__all__ = ["load_batch_actor", "load_partition_actor"]
