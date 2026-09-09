from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.scope_contracts import ActorContext, ScopeGrant

if TYPE_CHECKING:
    import sqlite3

_GRANT: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_ADMISSION: TypeAdapter[tuple[str, int, str] | None] = TypeAdapter(tuple[str, int, str] | None)


def admit_channel_grant(
    db: sqlite3.Connection, actor: ActorContext, grant: ScopeGrant, *, fresh: bool
) -> None:
    if grant.scope.channel_id is None:
        return
    key = (actor.workspace_id, actor.member_id, scope_key(grant.scope), grant.capability.value)
    admission = _ADMISSION.validate_python(
        db.execute(
            """SELECT grant_id,policy_epoch,state FROM channel_grant_admissions
            WHERE workspace_id=? AND member_id=? AND scope_key=? AND capability=?""",
            key,
        ).fetchone()
    )
    if admission is None:
        if not fresh:
            raise KnowledgePolicyError(code="channel_grant_admission_missing")
    else:
        previous_id, epoch, state = admission
        if state != "active" or epoch > actor.policy_epoch:
            raise KnowledgePolicyError(code="channel_grant_revoked")
        previous = stored_grant(db, actor, previous_id)
        if previous.scope != grant.scope or previous.capability != grant.capability:
            raise KnowledgePolicyError(code="channel_grant_binding_mismatch")
        if epoch == actor.policy_epoch and previous_id != grant.grant_id:
            raise KnowledgePolicyError(code="channel_grant_binding_mismatch")
        if epoch != actor.policy_epoch and not fresh:
            raise KnowledgePolicyError(code="channel_grant_epoch_mismatch")
    _ = db.execute(
        """INSERT INTO channel_grant_admissions
        (workspace_id,member_id,scope_key,capability,grant_id,policy_epoch,state)
        VALUES (?,?,?,?,?,?,'active')
        ON CONFLICT(workspace_id,member_id,scope_key,capability) DO UPDATE SET
        grant_id=excluded.grant_id,policy_epoch=excluded.policy_epoch""",
        (*key, grant.grant_id, actor.policy_epoch),
    )


def stored_grant(db: sqlite3.Connection, actor: ActorContext, grant_id: str) -> ScopeGrant:
    row = _GRANT.validate_python(
        db.execute(
            """SELECT grant_json FROM scope_grants
            WHERE workspace_id=? AND member_id=? AND grant_id=?""",
            (actor.workspace_id, actor.member_id, grant_id),
        ).fetchone()
    )
    if row is None:
        raise KnowledgePolicyError(code="knowledge_ingress_grant_missing")
    grant = ScopeGrant.model_validate_json(row[0])
    now = max(actor.authenticated_at, datetime.now(UTC))
    if grant.effective_at > now or (grant.expires_at is not None and grant.expires_at <= now):
        raise KnowledgePolicyError(code="knowledge_ingress_grant_expired")
    return grant
