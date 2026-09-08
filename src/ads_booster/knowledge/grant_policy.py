from __future__ import annotations

# ruff: noqa: TC001, TC003
from dataclasses import dataclass
from datetime import datetime

from ads_booster.knowledge.contract_types import GrantCapability, ScopeKind
from ads_booster.knowledge.errors import (
    AccessDeniedError,
    PolicyEpochStaleError,
    ScopeIntersectionError,
)
from ads_booster.knowledge.governance_contracts import BrandTarget
from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext, ScopeGrant


@dataclass(frozen=True, slots=True)
class _GrantRequirement:
    capability: GrantCapability
    target_scope: AccessScope
    brand_id: str | None
    at: datetime


def require_current_policy_epoch(*, actor: ActorContext, current_epoch: int) -> None:
    if actor.policy_epoch != current_epoch:
        raise PolicyEpochStaleError(
            code="stale_policy_epoch",
            actor_epoch=actor.policy_epoch,
            current_epoch=current_epoch,
        )


def authorize_read(*, actor: ActorContext, target_scope: AccessScope, at: datetime) -> ScopeGrant:
    return _require_grant(
        actor,
        _GrantRequirement(GrantCapability.READ, target_scope, None, at),
    )


def authorize_write(*, actor: ActorContext, target_scope: AccessScope, at: datetime) -> ScopeGrant:
    if (
        actor.conversation_scope.kind is ScopeKind.MEMBER
        and target_scope.kind is ScopeKind.WORKSPACE
    ):
        raise AccessDeniedError(
            code="private_shared_write_forbidden",
            actor_id=actor.actor_id,
            target_workspace_id=target_scope.workspace_id,
        )
    return _require_grant(
        actor,
        _GrantRequirement(GrantCapability.WRITE, target_scope, None, at),
    )


def authorize_schedule(
    *, actor: ActorContext, target_scope: AccessScope, at: datetime
) -> ScopeGrant:
    _require_shared_actor(actor, target_scope, "private_schedule_forbidden")
    return _require_grant(
        actor,
        _GrantRequirement(GrantCapability.SCHEDULE, target_scope, None, at),
    )


def authorize_share(*, actor: ActorContext, target_scope: AccessScope, at: datetime) -> ScopeGrant:
    _require_shared_actor(actor, target_scope, "private_scope_expansion_forbidden")
    return _require_grant(
        actor,
        _GrantRequirement(GrantCapability.SHARE, target_scope, None, at),
    )


def authorize_purge(*, actor: ActorContext, target_scope: AccessScope, at: datetime) -> ScopeGrant:
    _require_shared_actor(actor, target_scope, "private_purge_forbidden")
    return _require_grant(
        actor,
        _GrantRequirement(GrantCapability.PURGE, target_scope, None, at),
    )


def authorize_brand_voice_edit(
    *, actor: ActorContext, target: BrandTarget, at: datetime
) -> ScopeGrant:
    _require_shared_actor(actor, target.scope, "private_brand_voice_edit_forbidden")
    return _require_grant(
        actor,
        _GrantRequirement(
            GrantCapability.BRAND_VOICE_EDIT,
            target.scope,
            target.brand_id,
            at,
        ),
    )


def intersect_lineage_scopes(scopes: tuple[AccessScope, ...]) -> AccessScope:
    if not scopes:
        raise ScopeIntersectionError(code="lineage_scope_empty", workspace_ids=())
    workspace_ids = tuple(dict.fromkeys(item.workspace_id for item in scopes))
    if len(workspace_ids) != 1:
        raise ScopeIntersectionError(
            code="lineage_scope_empty",
            workspace_ids=workspace_ids,
        )
    private_scopes = tuple(item for item in scopes if item.kind is ScopeKind.MEMBER)
    if not private_scopes:
        return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_ids[0])
    first = private_scopes[0]
    if any(item != first for item in private_scopes[1:]):
        raise ScopeIntersectionError(
            code="lineage_scope_empty",
            workspace_ids=workspace_ids,
        )
    return first


def _require_shared_actor(
    actor: ActorContext,
    target_scope: AccessScope,
    error_code: str,
) -> None:
    if actor.conversation_scope.kind is ScopeKind.MEMBER:
        raise AccessDeniedError(
            code=error_code,
            actor_id=actor.actor_id,
            target_workspace_id=target_scope.workspace_id,
        )


def _require_grant(actor: ActorContext, requirement: _GrantRequirement) -> ScopeGrant:
    if actor.workspace_id != requirement.target_scope.workspace_id:
        raise AccessDeniedError(
            code="workspace_scope_mismatch",
            actor_id=actor.actor_id,
            target_workspace_id=requirement.target_scope.workspace_id,
        )
    current = tuple(
        grant
        for grant in actor.grants
        if grant.capability is requirement.capability
        and grant.policy_epoch == actor.policy_epoch
        and grant.scope == requirement.target_scope
        and grant.brand_id == requirement.brand_id
        and grant.effective_at <= requirement.at
        and (grant.expires_at is None or requirement.at < grant.expires_at)
    )
    if len(current) != 1:
        raise AccessDeniedError(
            code="required_grant_missing",
            actor_id=actor.actor_id,
            target_workspace_id=requirement.target_scope.workspace_id,
        )
    return current[0]
