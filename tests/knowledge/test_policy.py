from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AuthenticatedEvent,
    AuthorityClass,
    AuthorityRef,
    BrandTarget,
    Claim,
    ClaimKind,
    ClaimStatus,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    InstructionAuthority,
    Provenance,
    ScopeGrant,
    ScopeKind,
)
from ads_booster.knowledge.errors import (
    AccessDeniedError,
    AuthorityViolationError,
    PolicyEpochStaleError,
    ScopeIntersectionError,
)
from ads_booster.knowledge.policy import (
    authorize_brand_voice_edit,
    authorize_read,
    authorize_schedule,
    authorize_write,
    intersect_lineage_scopes,
    require_claim_authority,
    require_current_policy_epoch,
)

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


def _workspace_scope(workspace_id: str = "workspace.team-a") -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_id)


def _private_scope(
    member_id: str = "member.a1",
    session_id: str = "session.dm.a1",
) -> AccessScope:
    return AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id="workspace.team-a",
        member_id=member_id,
        session_id=session_id,
    )


def _grant(
    capability: GrantCapability,
    scope: AccessScope,
    *,
    brand_id: str | None = None,
) -> ScopeGrant:
    return ScopeGrant(
        grant_id=f"grant.{capability.value}.{brand_id or scope.kind.value}",
        capability=capability,
        workspace_id=scope.workspace_id,
        scope=scope,
        brand_id=brand_id,
        policy_epoch=7,
        effective_at=NOW,
    )


def _actor(scope: AccessScope, grants: tuple[ScopeGrant, ...]) -> ActorContext:
    return ActorContext(
        actor_id=scope.member_id or "member.a1",
        workspace_id=scope.workspace_id,
        member_id=scope.member_id or "member.a1",
        session_id=scope.session_id or "session.channel.a1",
        conversation_scope=scope,
        grants=grants,
        policy_epoch=7,
        authenticated_at=NOW,
    )


def test_private_actor_can_read_shared_scope_with_verified_grant() -> None:
    private = _private_scope()
    shared = _workspace_scope()
    actor = _actor(private, (_grant(GrantCapability.READ, shared),))

    result = authorize_read(actor=actor, target_scope=shared, at=NOW)

    assert result.grant_id == "grant.read.workspace"


def test_private_actor_cannot_write_shared_scope_even_with_write_grant() -> None:
    private = _private_scope()
    shared = _workspace_scope()
    actor = _actor(private, (_grant(GrantCapability.WRITE, shared),))

    with pytest.raises(AccessDeniedError) as caught:
        _ = authorize_write(actor=actor, target_scope=shared, at=NOW)

    assert caught.value.code == "private_shared_write_forbidden"


def test_private_actor_cannot_schedule_shared_work() -> None:
    private = _private_scope()
    shared = _workspace_scope()
    actor = _actor(private, (_grant(GrantCapability.SCHEDULE, shared),))

    with pytest.raises(AccessDeniedError) as caught:
        _ = authorize_schedule(actor=actor, target_scope=shared, at=NOW)

    assert caught.value.code == "private_schedule_forbidden"


def test_forged_workspace_is_denied_before_grant_matching() -> None:
    shared = _workspace_scope()
    actor = _actor(shared, (_grant(GrantCapability.READ, shared),))

    with pytest.raises(AccessDeniedError) as caught:
        _ = authorize_read(
            actor=actor,
            target_scope=_workspace_scope("workspace.team-b"),
            at=NOW,
        )

    assert caught.value.code == "workspace_scope_mismatch"


def test_stale_policy_epoch_is_rejected() -> None:
    actor = _actor(_workspace_scope(), ())

    with pytest.raises(PolicyEpochStaleError) as caught:
        require_current_policy_epoch(actor=actor, current_epoch=8)

    assert caught.value.actor_epoch == 7
    assert caught.value.current_epoch == 8


def test_lineage_scope_intersection_narrows_shared_to_private() -> None:
    private = _private_scope()

    result = intersect_lineage_scopes((_workspace_scope(), private))

    assert result == private


def test_lineage_scope_intersection_rejects_cross_member_and_workspace() -> None:
    with pytest.raises(ScopeIntersectionError, match="lineage_scope_empty"):
        _ = intersect_lineage_scopes(
            (_private_scope(), _private_scope("member.a2", "session.dm.a2"))
        )
    with pytest.raises(ScopeIntersectionError, match="lineage_scope_empty"):
        _ = intersect_lineage_scopes((_workspace_scope(), _workspace_scope("workspace.team-b")))


def test_brand_voice_edit_needs_workspace_context_and_exact_brand_grant() -> None:
    shared = _workspace_scope()
    actor = _actor(
        shared,
        (_grant(GrantCapability.BRAND_VOICE_EDIT, shared, brand_id="brand.a"),),
    )

    result = authorize_brand_voice_edit(
        actor=actor,
        target=BrandTarget(scope=shared, brand_id="brand.a"),
        at=NOW,
    )

    assert result.brand_id == "brand.a"
    with pytest.raises(AccessDeniedError):
        _ = authorize_brand_voice_edit(
            actor=actor,
            target=BrandTarget(scope=shared, brand_id="brand.b"),
            at=NOW,
        )


def test_decision_authority_requires_matching_authenticated_human_event() -> None:
    scope = _workspace_scope()
    authority = AuthorityRef(
        event_id="event.decision.1",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="member.a1",
        workspace_id=scope.workspace_id,
        scope=scope,
        policy_epoch=7,
    )
    claim = Claim(
        claim_id="claim.launch.market",
        kind=ClaimKind.DECISION,
        statement="Korea is the initial launch market.",
        status=ClaimStatus.ACTIVE,
        evidence_refs=(
            EvidenceRef(
                evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                evidence_id="event.decision.1",
                revision_id="source.launch.decision.rev1",
                scope=scope,
                instruction_authority=InstructionAuthority.AUTHORIZED_USER,
                provenance=Provenance.HUMAN_DIRECT,
            ),
        ),
        authority_ref=authority,
        admission_reason="Direct launch decision.",
        observed_at=NOW,
    )
    actor = _actor(scope, ())
    external = AuthenticatedEvent(
        event_id="event.decision.1",
        actor_ref="member.a1",
        workspace_id=scope.workspace_id,
        scope=scope,
        provenance=Provenance.EXTERNAL,
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        capabilities=(),
        policy_epoch=7,
        occurred_at=NOW,
    )

    with pytest.raises(AuthorityViolationError, match="decision_requires_human_direct_event"):
        _ = require_claim_authority(claim=claim, actor=actor, events=(external,))

    direct = external.model_copy(update={"provenance": Provenance.HUMAN_DIRECT})
    injected = claim.model_copy(
        update={
            "evidence_refs": (
                EvidenceRef(
                    evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                    evidence_id="event.decision.1",
                    revision_id="source.launch.decision.rev1",
                    scope=scope,
                ),
            )
        }
    )
    with pytest.raises(AuthorityViolationError, match="decision_evidence_not_authoritative"):
        _ = require_claim_authority(claim=injected, actor=actor, events=(direct,))
    assert require_claim_authority(claim=claim, actor=actor, events=(direct,)) == direct
