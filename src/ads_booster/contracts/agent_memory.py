"""Governed memory is sourced data, never a tool execution grant."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves runtime field annotations.
from enum import StrEnum, unique
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class MemoryScope(ContractModel):
    workspace_id: Identifier
    product_id: str = ""
    campaign_id: str = ""
    work_id: str = ""
    member_id: str = ""
    session_id: str = ""
    channel_id: Identifier | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_digest(self, handler: SerializerFunctionWrapHandler) -> JsonObject:
        """Preserve persisted observation keys and source hashes when no channel was recorded."""
        result = _JSON_OBJECT.validate_python(handler(self))
        if self.channel_id is None:
            _ = result.pop("channel_id", None)
        return result

    @model_validator(mode="after")
    def complete_private_scope(self) -> Self:
        if bool(self.member_id) != bool(self.session_id):
            message = "memory_private_scope_requires_member_and_session"
            raise ValueError(message)
        return self


class MemoryAccess(ContractModel):
    """Application-issued authority; never deserialize this from model or document content."""

    scope: MemoryScope
    actor_id: Identifier
    private: bool = False
    can_review: bool = False

    @model_validator(mode="after")
    def private_identity(self) -> Self:
        if self.private != bool(self.scope.member_id):
            message = "memory_access_privacy_mismatch"
            raise ValueError(message)
        if self.private and self.actor_id != self.scope.member_id:
            message = "memory_access_member_mismatch"
            raise ValueError(message)
        return self


MemoryStage = Literal["candidate", "review", "approved", "rejected"]


class MemoryNote(ContractModel):
    schema_version: Literal["trace.agent-memory.v1"] = "trace.agent-memory.v1"
    note_id: Identifier
    scope: MemoryScope
    category: Literal[
        "fact", "observation", "preference", "hypothesis", "proposal", "approval_decision"
    ]
    domain: Literal[
        "product_brand", "team_operations", "market_customer", "asset_format", "work", "learning"
    ]
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    source_ref: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Sha256Digest
    author_id: Identifier
    created_at: datetime
    expires_at: datetime
    version: Annotated[int, Field(ge=1)] = 1
    supersedes: str = ""
    conflicts: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    stage: MemoryStage = "candidate"

    @model_validator(mode="after")
    def valid_lifetime(self) -> Self:
        require_aware(self.created_at)
        require_aware(self.expires_at)
        if self.expires_at <= self.created_at:
            message = "memory_expiry_not_after_creation"
            raise ValueError(message)
        if self.note_id in self.conflicts or self.note_id == self.supersedes:
            message = "memory_self_reference"
            raise ValueError(message)
        return self


class MemoryReference(ContractModel):
    note_id: Identifier
    sha256: Sha256Digest


@unique
class LegacyMemoryAssessmentKind(StrEnum):
    COMPATIBLE = "compatible"
    UNRELATED = "unrelated"
    CONFLICT = "conflict"


class LegacyMemoryAssessment(ContractModel):
    """One model judgment bound to a server-selected approved legacy note."""

    selection_sha256: Sha256Digest
    reference: MemoryReference
    assessment: LegacyMemoryAssessmentKind


class MemorySelectionReceipt(ContractModel):
    selection_id: Identifier
    run_id: Identifier
    scope: MemoryScope
    selected: tuple[MemoryReference, ...]
    query_sha256: Sha256Digest
    selected_at: datetime
    authority: Literal["data_only_not_execution_approval"] = "data_only_not_execution_approval"
    actor_id: Identifier | None = None
    selection_sha256: Sha256Digest | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> JsonObject:
        result = _JSON_OBJECT.validate_python(handler(self))
        if self.actor_id is None:
            _ = result.pop("actor_id", None)
        if self.selection_sha256 is None:
            _ = result.pop("selection_sha256", None)
        return result

    def canonical_sha256(self) -> Sha256Digest:
        """Bind semantic selection identity without timestamps or random receipt IDs."""
        return contract_sha256(
            {
                "run_id": self.run_id,
                "scope": self.scope.model_dump(mode="json", exclude_defaults=True),
                "actor_id": self.actor_id,
                "selected": [item.model_dump(mode="json") for item in self.selected],
                "query_sha256": self.query_sha256,
                "authority": self.authority,
            }
        )


class MemorySelection(ContractModel):
    notes: tuple[MemoryNote, ...]
    receipt: MemorySelectionReceipt


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "memory_datetime_requires_timezone"
        raise ValueError(message)
