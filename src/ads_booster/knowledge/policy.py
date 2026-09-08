from __future__ import annotations

# ruff: noqa: TC001
from dataclasses import dataclass

from ads_booster.knowledge.contract_types import (
    ClaimKind,
    GrantCapability,
    InstructionAuthority,
    MemoryKind,
    MemoryStatus,
    Provenance,
    UsageRole,
)
from ads_booster.knowledge.errors import AuthorityViolationError
from ads_booster.knowledge.evidence_contracts import AuthenticatedEvent
from ads_booster.knowledge.grant_policy import (
    authorize_brand_voice_edit,
    authorize_purge,
    authorize_read,
    authorize_schedule,
    authorize_share,
    authorize_write,
    intersect_lineage_scopes,
    require_current_policy_epoch,
)
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryEntry
from ads_booster.knowledge.scope_contracts import ActorContext
from ads_booster.knowledge.wiki_contracts import Claim


@dataclass(frozen=True, slots=True)
class MemoryAuthorityContext:
    document: MemoryDocument
    actor: ActorContext
    events: tuple[AuthenticatedEvent, ...]


def require_claim_authority(
    *,
    claim: Claim,
    actor: ActorContext,
    events: tuple[AuthenticatedEvent, ...],
) -> AuthenticatedEvent | None:
    match claim.kind:  # noqa: MATCH_OK
        case ClaimKind.FACT | ClaimKind.INFERENCE:
            return None
        case ClaimKind.DECISION:
            authority = claim.authority_ref
            if authority is None:
                raise AuthorityViolationError(
                    code="decision_requires_authority",
                    target_id=claim.claim_id,
                )
            event = next((item for item in events if item.event_id == authority.event_id), None)
            if event is None or event.provenance is not Provenance.HUMAN_DIRECT:
                raise AuthorityViolationError(
                    code="decision_requires_human_direct_event",
                    target_id=claim.claim_id,
                )
            if not any(
                item.evidence_id == authority.event_id
                and item.instruction_authority is InstructionAuthority.AUTHORIZED_USER
                and item.provenance is Provenance.HUMAN_DIRECT
                for item in claim.evidence_refs
            ):
                raise AuthorityViolationError(
                    code="decision_evidence_not_authoritative",
                    target_id=claim.claim_id,
                )
            if (
                authority.workspace_id != actor.workspace_id
                or event.workspace_id != actor.workspace_id
                or authority.actor_ref != event.actor_ref
                or authority.policy_epoch != actor.policy_epoch
                or event.policy_epoch != actor.policy_epoch
                or intersect_lineage_scopes((authority.scope, event.scope)) != authority.scope
            ):
                raise AuthorityViolationError(
                    code="decision_authority_binding_mismatch",
                    target_id=claim.claim_id,
                )
            return event


def require_memory_authority(
    *, entry: MemoryEntry, context: MemoryAuthorityContext
) -> AuthenticatedEvent | None:
    match entry.usage_role:  # noqa: MATCH_OK
        case UsageRole.REFERENCE:
            if context.document.kind is not MemoryKind.SOUL:
                return None
        case UsageRole.CONSTRAINT:
            pass
    authority = entry.authority_ref
    if authority is None:
        raise AuthorityViolationError(
            code="memory_authority_required",
            target_id=entry.entry_id,
        )
    event = next((item for item in context.events if item.event_id == authority.event_id), None)
    if event is None or event.provenance is not Provenance.HUMAN_DIRECT:
        raise AuthorityViolationError(
            code="memory_requires_human_direct_event",
            target_id=entry.entry_id,
        )
    if not any(
        item.evidence_id == authority.event_id
        and item.instruction_authority is InstructionAuthority.AUTHORIZED_USER
        and item.provenance is Provenance.HUMAN_DIRECT
        for item in entry.source_refs
    ):
        raise AuthorityViolationError(
            code="memory_evidence_not_authoritative",
            target_id=entry.entry_id,
        )
    if context.document.kind is MemoryKind.SOUL and (
        context.document.brand_id is None
        or (
            entry.status is MemoryStatus.ACTIVE
            and GrantCapability.BRAND_VOICE_EDIT not in event.capabilities
        )
    ):
        raise AuthorityViolationError(
            code="soul_authority_required",
            target_id=entry.entry_id,
        )
    if (
        authority.workspace_id != context.actor.workspace_id
        or event.workspace_id != context.actor.workspace_id
        or context.document.workspace_id != context.actor.workspace_id
        or entry.scope.workspace_id != context.actor.workspace_id
        or authority.actor_ref != event.actor_ref
        or authority.authority_class is not event.authority_class
        or authority.policy_epoch != context.actor.policy_epoch
        or event.policy_epoch != context.actor.policy_epoch
        or intersect_lineage_scopes((entry.scope, authority.scope, event.scope)) != entry.scope
    ):
        raise AuthorityViolationError(
            code="memory_authority_binding_mismatch",
            target_id=entry.entry_id,
        )
    return event


__all__ = [
    "MemoryAuthorityContext",
    "authorize_brand_voice_edit",
    "authorize_purge",
    "authorize_read",
    "authorize_schedule",
    "authorize_share",
    "authorize_write",
    "intersect_lineage_scopes",
    "require_claim_authority",
    "require_current_policy_epoch",
    "require_memory_authority",
]
