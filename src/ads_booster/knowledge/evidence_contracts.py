from __future__ import annotations

# ruff: noqa: TC001
from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    EvidenceKind,
    GrantCapability,
    InstructionAuthority,
    KnowledgeContractModel,
    Provenance,
    UtcDatetime,
)
from ads_booster.knowledge.scope_contracts import AccessScope

AUTHORITY_SCOPE_WORKSPACE_MISMATCH = "authority_scope_workspace_mismatch"
AUTHORITY_SCOPE_WORKSPACE_MESSAGE = "authority workspace must match its scope"
AUTHENTICATED_EVENT_SCOPE_WORKSPACE_MISMATCH = "authenticated_event_scope_workspace_mismatch"
AUTHENTICATED_EVENT_SCOPE_WORKSPACE_MESSAGE = "authenticated event workspace must match its scope"


class EvidenceRef(KnowledgeContractModel):
    """Bounded immutable pointer; its presence does not certify semantic truth."""

    evidence_kind: EvidenceKind
    evidence_id: BoundedId
    revision_id: BoundedId
    segment_id: BoundedId | None = None
    quote_sha256: Sha256Digest | None = None
    scope: AccessScope
    instruction_authority: InstructionAuthority = InstructionAuthority.DATA
    provenance: Provenance = Provenance.EXTERNAL


class AuthorityRef(KnowledgeContractModel):
    """Claimed authority pointer checked against an authenticated event by policy."""

    event_id: BoundedId
    authority_class: AuthorityClass
    actor_ref: BoundedId
    workspace_id: BoundedId
    scope: AccessScope
    policy_epoch: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def require_scope_workspace(self) -> Self:
        if self.scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                AUTHORITY_SCOPE_WORKSPACE_MISMATCH,
                AUTHORITY_SCOPE_WORKSPACE_MESSAGE,
            )
        return self


class AuthenticatedEvent(KnowledgeContractModel):
    """Trusted adapter output, never accepted from model-authored tool input."""

    event_id: BoundedId
    actor_ref: BoundedId
    workspace_id: BoundedId
    scope: AccessScope
    instruction_authority: InstructionAuthority = InstructionAuthority.AUTHORIZED_USER
    provenance: Provenance
    authority_class: AuthorityClass
    capabilities: Annotated[tuple[GrantCapability, ...], Field(max_length=16)] = ()
    policy_epoch: Annotated[int, Field(ge=1)]
    occurred_at: UtcDatetime

    @model_validator(mode="after")
    def require_scope_workspace(self) -> Self:
        if self.scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                AUTHENTICATED_EVENT_SCOPE_WORKSPACE_MISMATCH,
                AUTHENTICATED_EVENT_SCOPE_WORKSPACE_MESSAGE,
            )
        return self
