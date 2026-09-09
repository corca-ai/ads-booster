from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import GrantCapability
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.grant_policy import (
    authorize_read,
    authorize_write,
    require_current_policy_epoch,
)
from ads_booster.knowledge.scope_contracts import ActorContext, ScopeGrant, channel_member_scope

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.operation_contracts import KnowledgeJob
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import AccessScope

_IDENTITY: TypeAdapter[tuple[str, int] | None] = TypeAdapter(tuple[str, int] | None)
_GRANTS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


def load_batch_actor(
    repository: SqliteKnowledgeRepository,
    scope: AccessScope,
    now: datetime,
    *,
    submitter: ActorContext | None = None,
) -> ActorContext:
    member_id = scope.member_id if submitter is None else submitter.member_id
    session_id = scope.session_id if submitter is None else submitter.session_id
    if member_id is None or session_id is None:
        raise KnowledgePolicyError(code="knowledge_batch_scope_identity_missing")
    conversation_scope = scope if submitter is None else submitter.conversation_scope
    permitted_scopes = (
        (conversation_scope,)
        if submitter is None
        else (
            conversation_scope,
            channel_member_scope(submitter),
        )
    )
    if scope not in permitted_scopes:
        raise KnowledgePolicyError(code="knowledge_batch_submitter_scope_mismatch")
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
                AND membership.state='active' AND membership.role IN ('editor','admin')
                AND session.state='active'""",
                (scope.workspace_id, member_id, session_id),
            ).fetchone()
        )
        if identity is None:
            raise KnowledgePolicyError(code="knowledge_batch_actor_unavailable")
        if submitter is not None:
            if identity[0] != submitter.actor_id:
                raise KnowledgePolicyError(code="knowledge_batch_actor_unavailable")
            require_current_policy_epoch(actor=submitter, current_epoch=identity[1])
        rows = _GRANTS.validate_python(
            connection.execute(
                """SELECT grant_json FROM scope_grants AS current
            WHERE workspace_id=? AND member_id=? AND policy_epoch=?
            AND NOT EXISTS (
                SELECT 1 FROM channel_grant_admissions AS admission
                WHERE admission.workspace_id=current.workspace_id
                    AND admission.member_id=current.member_id
                    AND admission.scope_key=current.scope_key
                    AND admission.capability=current.capability
                    AND (admission.state!='active' OR admission.grant_id!=current.grant_id
                        OR admission.policy_epoch!=current.policy_epoch)
            ) ORDER BY grant_id""",
                (scope.workspace_id, member_id, identity[1]),
            ).fetchall()
        )
    actor = ActorContext(
        actor_id=identity[0],
        workspace_id=scope.workspace_id,
        member_id=member_id,
        session_id=session_id,
        conversation_scope=conversation_scope,
        grants=tuple(
            grant
            for (encoded,) in rows
            for grant in (ScopeGrant.model_validate_json(encoded),)
            if grant.scope in permitted_scopes
            and (
                submitter is None
                or grant.grant_id in {bound.grant_id for bound in submitter.grants}
            )
        ),
        policy_epoch=identity[1],
        authenticated_at=now,
    )
    _ = authorize_read(actor=actor, target_scope=scope, at=now)
    _ = authorize_write(actor=actor, target_scope=scope, at=now)
    for bound in (() if submitter is None else submitter.grants):
        match bound.capability:
            case GrantCapability.READ:
                _ = authorize_read(actor=actor, target_scope=bound.scope, at=now)
            case GrantCapability.WRITE:
                _ = authorize_write(actor=actor, target_scope=bound.scope, at=now)
            case (
                GrantCapability.SCHEDULE
                | GrantCapability.SHARE
                | GrantCapability.PURGE
                | GrantCapability.BRAND_VOICE_EDIT
            ):
                continue
            case _:
                assert_never(bound.capability)
    return actor


def load_job_actor(
    repository: SqliteKnowledgeRepository,
    job: KnowledgeJob,
    fallback: ActorContext,
    now: datetime,
) -> ActorContext:
    if job.submitter_actor is not None:
        return load_batch_actor(repository, job.scope, now, submitter=job.submitter_actor)
    if job.scope == fallback.conversation_scope:
        return fallback
    return load_batch_actor(repository, job.scope, now)


def load_partition_actor(
    repository: SqliteKnowledgeRepository,
    bound: ActorContext,
    now: datetime,
) -> ActorContext:
    """Reload the exact member and session retained by a learning partition."""
    if bound.conversation_scope.channel_id is not None:
        return load_batch_actor(repository, bound.conversation_scope, now, submitter=bound)
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


__all__ = ["load_batch_actor", "load_job_actor", "load_partition_actor"]
