"""Project authenticated channel identities into existing knowledge authority rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import ActorContext, GrantCapability, ScopeGrant, ScopeKind
from ads_booster.knowledge.errors import AccessDeniedError
from ads_booster.knowledge.grant_policy import require_current_policy_epoch
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_identity import register_actor, scope_key
from ads_booster.knowledge.repository_types import MembershipRole

_WORKSPACE: TypeAdapter[tuple[int, str] | None] = TypeAdapter(tuple[int, str] | None)
_MEMBER: TypeAdapter[tuple[str, str, str, str] | None] = TypeAdapter(
    tuple[str, str, str, str] | None
)
_SESSION: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


@dataclass(frozen=True, slots=True)
class KnowledgeIngressAuthority:
    repository: SqliteKnowledgeRepository

    def bind_actor(self, actor: ActorContext, *, fresh: bool = False) -> ActorContext:
        """Bind conversation grants while preserving stored revocations and roles."""
        with self.repository.connection() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            workspace = _WORKSPACE.validate_python(
                db.execute(
                    "SELECT policy_epoch,state FROM workspaces WHERE workspace_id=?",
                    (actor.workspace_id,),
                ).fetchone()
            )
            if workspace is None or workspace[1] != "active":
                self._deny(actor)
            epoch = workspace[0] if fresh else actor.policy_epoch
            actor = actor.model_copy(update={"policy_epoch": epoch})
            require_current_policy_epoch(actor=actor, current_epoch=workspace[0])
            member = _MEMBER.validate_python(
                db.execute(
                    """SELECT member.member_id,member.state,membership.role,membership.state
                FROM members AS member JOIN memberships AS membership USING(workspace_id,member_id)
                WHERE member.workspace_id=? AND member.actor_id=?""",
                    (actor.workspace_id, actor.actor_id),
                ).fetchone()
            )
            if member is not None:
                if member[1] != "active" or member[3] != "active":
                    self._deny(actor)
                if member[2] == "reader" and any(
                    grant.capability is not GrantCapability.READ for grant in actor.grants
                ):
                    self._deny(actor)
                member_id = member[0]
            else:
                collision = _SESSION.validate_python(
                    db.execute(
                        "SELECT actor_id FROM members WHERE workspace_id=? AND member_id=?",
                        (actor.workspace_id, actor.member_id),
                    ).fetchone()
                )
                if collision is not None:
                    self._deny(actor)
                member_id = actor.member_id
            actor = self._conversation_actor(actor, member_id)
            if member is None:
                register_actor(db, actor, MembershipRole.EDITOR)
                return actor
            session = _SESSION.validate_python(
                db.execute(
                    """SELECT state FROM sessions
                    WHERE workspace_id=? AND member_id=? AND session_id=?""",
                    (actor.workspace_id, member_id, actor.session_id),
                ).fetchone()
            )
            if session is not None and session[0] != "active":
                self._deny(actor)
            _ = db.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?,?,?,'active')",
                (actor.workspace_id, member_id, actor.session_id),
            )
            for grant in actor.grants:
                grant_scope = grant.scope
                _ = db.execute(
                    "INSERT OR IGNORE INTO access_scopes VALUES (?,?,?,?,?,?)",
                    (
                        scope_key(grant_scope),
                        grant_scope.kind.value,
                        actor.workspace_id,
                        grant_scope.member_id,
                        grant_scope.session_id,
                        grant_scope.model_dump_json(),
                    ),
                )
                _ = db.execute(
                    """INSERT OR IGNORE INTO scope_grants(
                    workspace_id,grant_id,member_id,scope_key,capability,brand_id,
                    policy_epoch,effective_at,expires_at,grant_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        actor.workspace_id,
                        grant.grant_id,
                        member_id,
                        scope_key(grant_scope),
                        grant.capability.value,
                        grant.brand_id,
                        grant.policy_epoch,
                        grant.effective_at.isoformat(),
                        None if grant.expires_at is None else grant.expires_at.isoformat(),
                        grant.model_dump_json(),
                    ),
                )
            return actor

    @staticmethod
    def _conversation_actor(actor: ActorContext, member_id: str) -> ActorContext:
        scope = actor.conversation_scope
        if scope.kind is ScopeKind.MEMBER:
            scope = scope.model_copy(update={"member_id": member_id})
        grants: list[ScopeGrant] = []
        for grant in actor.grants:
            if grant.capability not in {GrantCapability.READ, GrantCapability.WRITE}:
                KnowledgeIngressAuthority._deny(actor)
            grant_scope = scope if grant.scope.kind is ScopeKind.MEMBER else grant.scope
            grant_id = (
                "conversation-grant-"
                + contract_sha256(
                    {
                        "actor": actor.actor_id,
                        "member": member_id,
                        "scope": grant_scope.model_dump(mode="json"),
                        "capability": grant.capability.value,
                        "epoch": actor.policy_epoch,
                    }
                )[:40]
            )
            grants.append(
                grant.model_copy(
                    update={
                        "grant_id": grant_id,
                        "scope": grant_scope,
                        "policy_epoch": actor.policy_epoch,
                    }
                )
            )
        return actor.model_copy(
            update={
                "member_id": member_id,
                "conversation_scope": scope,
                "grants": tuple(grants),
            }
        )

    @staticmethod
    def _deny(actor: ActorContext) -> Never:
        raise AccessDeniedError(
            code="knowledge_ingress_actor_denied",
            actor_id=actor.actor_id,
            target_workspace_id=actor.workspace_id,
        )
