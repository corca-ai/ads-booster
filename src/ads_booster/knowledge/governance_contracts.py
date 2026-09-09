from __future__ import annotations

# ruff: noqa: EM101, TC001
from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    BoundedReason,
    KnowledgeContractModel,
    ScopeKind,
    UtcDatetime,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef
from ads_booster.knowledge.operation_enums import (
    BrandEventKind,
    BrandState,
    ConstraintCompatibility,
    TaskBindingState,
)
from ads_booster.knowledge.scope_contracts import AccessScope


def _scope_absent(value: AccessScope | None) -> bool:
    return value is None


class AppliesTo(KnowledgeContractModel):
    action_kinds: Annotated[tuple[KnowledgeActionKind, ...], Field(max_length=16)] = ()
    task_ref: BoundedId | None = None
    product_refs: Annotated[tuple[BoundedId, ...], Field(max_length=32)] = ()
    subject_key: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class ConstraintBinding(KnowledgeContractModel):
    constraint_id: BoundedId
    workspace_id: BoundedId
    entry_id: BoundedId
    revision_id: BoundedId
    applies_to: AppliesTo
    authority_ref: AuthorityRef
    authority_class: AuthorityClass
    overrides_ids: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()
    supersedes_ids: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()
    compatibility: ConstraintCompatibility
    conflict_refs: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def require_authority_binding(self) -> Self:
        if (
            self.authority_ref.workspace_id != self.workspace_id
            or self.authority_ref.authority_class is not self.authority_class
        ):
            raise PydanticCustomError(
                "constraint_authority_mismatch",
                "constraint authority must match its workspace and class",
            )
        return self


class Brand(KnowledgeContractModel):
    brand_id: BoundedId
    workspace_id: BoundedId
    name: Annotated[str, Field(min_length=1, max_length=200)]
    revision: Annotated[int, Field(ge=1)]
    state: BrandState
    scope: AccessScope | None = Field(default=None, exclude_if=_scope_absent)

    @property
    def owned_scope(self) -> AccessScope:
        return self.scope or AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=self.workspace_id)

    @model_validator(mode="after")
    def require_shared_owner(self) -> Self:
        if self.owned_scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "brand_scope_workspace_mismatch", "brand scope must match its workspace"
            )
        if self.owned_scope.kind is ScopeKind.MEMBER:
            raise PydanticCustomError(
                "brand_scope_private", "brands require workspace or channel ownership"
            )
        return self


class BrandEvent(KnowledgeContractModel):
    event_id: BoundedId
    kind: BrandEventKind
    brand_id: BoundedId
    workspace_id: BoundedId
    expected_revision: Annotated[int | None, Field(ge=1)] = None
    authority_ref: AuthorityRef
    occurred_at: UtcDatetime

    @model_validator(mode="after")
    def require_authority_workspace(self) -> Self:
        if self.authority_ref.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "brand_event_authority_workspace_mismatch",
                "brand event authority must belong to its workspace",
            )
        return self


class BrandTarget(KnowledgeContractModel):
    scope: AccessScope
    brand_id: BoundedId


class TaskBinding(KnowledgeContractModel):
    task_id: BoundedId
    workspace_id: BoundedId
    actor_ref: BoundedId
    member_id: BoundedId
    session_id: BoundedId
    action_kind: KnowledgeActionKind
    brand_id: BoundedId | None = None
    brand_catalog_revision: Annotated[int | None, Field(ge=1)] = None
    capability_epoch: Annotated[int, Field(ge=1)]
    state: TaskBindingState
    opened_at: UtcDatetime
    closed_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def require_task_lifecycle(self) -> Self:
        if self.state is TaskBindingState.ACTIVE and self.closed_at is not None:
            raise PydanticCustomError(
                "active_task_forbids_closed_at",
                "active task bindings cannot have a close time",
            )
        if self.state is TaskBindingState.CLOSED and self.closed_at is None:
            raise PydanticCustomError(
                "closed_task_requires_closed_at",
                "closed task bindings require a close time",
            )
        return self


class TaskOverlay(KnowledgeContractModel):
    overlay_id: BoundedId
    task_id: BoundedId
    workspace_id: BoundedId
    actor_ref: BoundedId
    capability_epoch: Annotated[int, Field(ge=1)]
    brand_id: BoundedId | None = None
    instruction: Annotated[str, Field(min_length=1, max_length=4_000)]
    authority_ref: AuthorityRef
    reason: BoundedReason
    created_at: UtcDatetime
