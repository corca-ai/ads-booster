from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.contracts import AccessScope, ActorContext
    from ads_booster.knowledge.repository_types import MembershipRole


def scope_key(scope: AccessScope) -> str:
    return contract_sha256(scope)


def register_actor(
    connection: sqlite3.Connection,
    actor: ActorContext,
    role: MembershipRole,
) -> None:
    _ = connection.execute(
        """
        INSERT INTO workspaces(workspace_id,policy_epoch,timezone,state)
        VALUES (?,?,'UTC','active')
        ON CONFLICT(workspace_id) DO UPDATE SET policy_epoch=excluded.policy_epoch
        """,
        (actor.workspace_id, actor.policy_epoch),
    )
    _ = connection.execute(
        """
        INSERT INTO members(workspace_id,member_id,actor_id,state) VALUES (?,?,?,'active')
        ON CONFLICT(workspace_id,member_id) DO UPDATE
        SET actor_id=excluded.actor_id,state='active'
        """,
        (actor.workspace_id, actor.member_id, actor.actor_id),
    )
    _ = connection.execute(
        """
        INSERT INTO sessions(workspace_id,member_id,session_id,state) VALUES (?,?,?,'active')
        ON CONFLICT(workspace_id,member_id,session_id) DO UPDATE SET state='active'
        """,
        (actor.workspace_id, actor.member_id, actor.session_id),
    )
    scopes = {scope_key(actor.conversation_scope): actor.conversation_scope}
    for grant in actor.grants:
        scopes[scope_key(grant.scope)] = grant.scope
    for key, scope in scopes.items():
        _ = connection.execute(
            """
            INSERT INTO access_scopes(
                scope_key,kind,workspace_id,member_id,session_id,scope_json,channel_id
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(scope_key) DO NOTHING
            """,
            (
                key,
                scope.kind.value,
                scope.workspace_id,
                scope.member_id,
                scope.session_id,
                scope.model_dump_json(),
                scope.channel_id,
            ),
        )
    _ = connection.execute(
        """
        INSERT INTO memberships(workspace_id,member_id,role,revision,state)
        VALUES (?,?,?,1,'active')
        ON CONFLICT(workspace_id,member_id) DO UPDATE
        SET role=excluded.role,revision=memberships.revision+1,state='active'
        """,
        (actor.workspace_id, actor.member_id, role.value),
    )
    for grant in actor.grants:
        _ = connection.execute(
            """
            INSERT INTO scope_grants(
                workspace_id,grant_id,member_id,scope_key,capability,brand_id,
                policy_epoch,effective_at,expires_at,grant_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(workspace_id,grant_id) DO UPDATE SET
                member_id=excluded.member_id,scope_key=excluded.scope_key,
                capability=excluded.capability,brand_id=excluded.brand_id,
                policy_epoch=excluded.policy_epoch,effective_at=excluded.effective_at,
                expires_at=excluded.expires_at,grant_json=excluded.grant_json
            """,
            (
                actor.workspace_id,
                grant.grant_id,
                actor.member_id,
                scope_key(grant.scope),
                grant.capability.value,
                grant.brand_id,
                grant.policy_epoch,
                grant.effective_at.isoformat(),
                None if grant.expires_at is None else grant.expires_at.isoformat(),
                grant.model_dump_json(),
            ),
        )


__all__ = ["register_actor", "scope_key"]
