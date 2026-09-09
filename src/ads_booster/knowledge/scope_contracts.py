from __future__ import annotations

# ruff: noqa: EM101, TC001
from typing import Annotated, Self, assert_never

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.knowledge.contract_types import (
    GrantCapability,
    KnowledgeContractModel,
    ScopeKind,
    UtcDatetime,
)


def _missing_channel(value: str | None) -> bool:
    return value is None


class AccessScope(KnowledgeContractModel):
    kind: ScopeKind
    workspace_id: BoundedId
    member_id: BoundedId | None = None
    session_id: BoundedId | None = None
    channel_id: BoundedId | None = Field(default=None, exclude_if=_missing_channel)

    @model_validator(mode="after")
    def require_scope_identity(self) -> Self:
        match self.kind:  # noqa: MATCH_OK
            case ScopeKind.WORKSPACE:
                if any(
                    value is not None
                    for value in (self.member_id, self.session_id, self.channel_id)
                ):
                    raise PydanticCustomError(
                        "invalid_workspace_scope",
                        "workspace scope cannot contain channel, member or session identity",
                    )
            case ScopeKind.CHANNEL:
                if (
                    self.channel_id is None
                    or self.member_id is not None
                    or self.session_id is not None
                ):
                    raise PydanticCustomError(
                        "invalid_channel_scope",
                        "channel scope requires a channel and forbids member and session identity",
                    )
            case ScopeKind.CHANNEL_MEMBER:
                if self.channel_id is None or self.member_id is None or self.session_id is not None:
                    raise PydanticCustomError(
                        "invalid_channel_member_scope",
                        "personal scope requires channel and member and forbids session identity",
                    )
            case ScopeKind.MEMBER:
                if self.member_id is None or self.session_id is None or self.channel_id is not None:
                    raise PydanticCustomError(
                        "invalid_member_scope",
                        "member scope requires workspace, member, and session identity",
                    )
        return self

    def contains(self, other: AccessScope) -> bool:
        if self.workspace_id != other.workspace_id:
            return False
        match self.kind:
            case ScopeKind.WORKSPACE:
                return True
            case ScopeKind.CHANNEL:
                return self == other or (
                    other.kind is ScopeKind.CHANNEL_MEMBER and self.channel_id == other.channel_id
                )
            case ScopeKind.CHANNEL_MEMBER | ScopeKind.MEMBER:
                return self == other
        assert_never(self.kind)


class ScopeGrant(KnowledgeContractModel):
    grant_id: BoundedId
    capability: GrantCapability
    workspace_id: BoundedId
    scope: AccessScope
    brand_id: BoundedId | None = None
    policy_epoch: Annotated[int, Field(ge=1)]
    effective_at: UtcDatetime
    expires_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def require_grant_binding(self) -> Self:
        if self.scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "grant_workspace_mismatch",
                "grant workspace must match its scope",
            )
        match self.capability:  # noqa: MATCH_OK
            case GrantCapability.BRAND_VOICE_EDIT:
                if self.brand_id is None:
                    raise PydanticCustomError(
                        "brand_voice_grant_requires_brand",
                        "brand voice grants require a brand",
                    )
            case (
                GrantCapability.READ
                | GrantCapability.WRITE
                | GrantCapability.SCHEDULE
                | GrantCapability.SHARE
                | GrantCapability.PURGE
            ):
                if self.brand_id is not None:
                    raise PydanticCustomError(
                        "non_voice_grant_forbids_brand",
                        "only brand voice grants carry a brand",
                    )
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise PydanticCustomError(
                "grant_lifetime_not_ordered",
                "grant expiry must follow its effective time",
            )
        return self


class ActorContext(KnowledgeContractModel):
    """Authenticated principal supplied outside model-authored request payloads."""

    actor_id: BoundedId
    workspace_id: BoundedId
    member_id: BoundedId
    session_id: BoundedId
    conversation_scope: AccessScope
    grants: Annotated[tuple[ScopeGrant, ...], Field(max_length=256)] = ()
    policy_epoch: Annotated[int, Field(ge=1)]
    authenticated_at: UtcDatetime

    @model_validator(mode="after")
    def require_actor_binding(self) -> Self:
        if self.conversation_scope.kind is ScopeKind.CHANNEL_MEMBER:
            raise PydanticCustomError(
                "actor_personal_scope_not_conversation",
                "channel member scope owns personal memory, not conversations",
            )
        if self.conversation_scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "actor_workspace_mismatch",
                "actor workspace must match the conversation scope",
            )
        if self.conversation_scope.kind is ScopeKind.MEMBER and (
            self.conversation_scope.member_id != self.member_id
            or self.conversation_scope.session_id != self.session_id
        ):
            raise PydanticCustomError(
                "actor_private_scope_mismatch",
                "private scope must preserve actor member and session identity",
            )
        if any(grant.workspace_id != self.workspace_id for grant in self.grants):
            raise PydanticCustomError(
                "actor_grant_workspace_mismatch",
                "actor grants must belong to the actor workspace",
            )
        return self


def channel_member_scope(actor: ActorContext) -> AccessScope | None:
    match actor.conversation_scope.kind:
        case ScopeKind.CHANNEL:
            return AccessScope(
                kind=ScopeKind.CHANNEL_MEMBER,
                workspace_id=actor.workspace_id,
                channel_id=actor.conversation_scope.channel_id,
                member_id=actor.member_id,
            )
        case ScopeKind.WORKSPACE | ScopeKind.MEMBER | ScopeKind.CHANNEL_MEMBER:
            return None
    assert_never(actor.conversation_scope.kind)
