from __future__ import annotations

from enum import StrEnum, unique
from typing import Annotated, ClassVar, Literal, LiteralString, Self

from pydantic import ConfigDict, Field, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.knowledge_context import EvidenceExcerpt  # noqa: TC001
from ads_booster.contracts.knowledge_selection import (
    ContextReceipt,
    ContextRequest,
    KnowledgeActionKind,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.transport.json_types import JsonObject  # noqa: TC001


def _raise_contract_error(code: LiteralString, message: LiteralString) -> None:
    raise PydanticCustomError(code, message)


class KnowledgePreparationModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


@unique
class DegradedKnowledgeReason(StrEnum):
    INDEX_PENDING = "index_pending"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    SUMMARY_PENDING = "summary_pending"


@unique
class RequiredContextErrorCode(StrEnum):
    CORRECTION_PENDING = "correction_pending"
    REQUIRED_CONTEXT_DENIED = "required_context_denied"
    REQUIRED_CONTEXT_STALE = "required_context_stale"
    REQUIRED_CONTEXT_UNAVAILABLE = "required_context_unavailable"
    REQUIRED_CONTEXT_OVER_BUDGET = "required_context_over_budget"
    REQUIRED_VOICE_UNAVAILABLE = "required_voice_unavailable"
    CONSTRAINT_CONFLICT = "constraint_conflict"
    SCOPE_UNRESOLVED = "scope_unresolved"


@unique
class PreparedContextSlot(StrEnum):
    ROLE = "role"
    AUTHORITY = "authority"
    TOOL_CATALOG = "tool_catalog"
    STORAGE_GUIDE = "storage_guide"
    REQUEST = "request"
    CONSTRAINT = "constraint"
    BRAND_VOICE = "brand_voice"
    TASK_OVERLAY = "task_overlay"
    MEMORY = "memory"
    WIKI = "wiki"
    SKILL = "skill"


@unique
class PreparedContextRole(StrEnum):
    SYSTEM = "system"
    EDITORIAL = "editorial"
    DATA = "data"


class PreparedContextBlock(KnowledgePreparationModel):
    block_id: BoundedId
    slot: PreparedContextSlot
    role: PreparedContextRole
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    revision_refs: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()


class PreparedKnowledgeContext(KnowledgePreparationModel):
    schema_version: Literal["knowledge.prepared-context.v1"] = Field(alias="schema")
    request: ContextRequest
    receipt: ContextReceipt
    receipt_sha256: Sha256Digest
    blocks: Annotated[tuple[PreparedContextBlock, ...], Field(min_length=5, max_length=256)]
    evidence_excerpts: Annotated[tuple[EvidenceExcerpt, ...], Field(max_length=24)] = ()


class KnowledgePreparationReady(KnowledgePreparationModel):
    schema_version: Literal["knowledge.preparation.v1"] = Field(alias="schema")
    status: Literal["ready"]
    task_ref: BoundedId
    action_kind: KnowledgeActionKind
    brand_ref: BoundedId | None
    context_request_id: BoundedId
    context_receipt_id: BoundedId
    context_receipt_sha256: Sha256Digest


class KnowledgePreparationDegraded(KnowledgePreparationModel):
    schema_version: Literal["knowledge.preparation.v1"] = Field(alias="schema")
    status: Literal["degraded"]
    task_ref: BoundedId
    action_kind: KnowledgeActionKind
    brand_ref: BoundedId | None
    reason: DegradedKnowledgeReason
    context_receipt_id: BoundedId
    context_receipt_sha256: Sha256Digest


class RequiredContextPreparationError(KnowledgePreparationModel):
    schema_version: Literal["knowledge.preparation.v1"] = Field(alias="schema")
    status: Literal["required_context_error"]
    task_ref: BoundedId
    action_kind: KnowledgeActionKind
    brand_ref: BoundedId | None
    error_code: RequiredContextErrorCode


class BrandUnresolvedPreparation(KnowledgePreparationModel):
    schema_version: Literal["knowledge.preparation.v1"] = Field(alias="schema")
    status: Literal["brand_unresolved"]
    task_ref: BoundedId
    action_kind: KnowledgeActionKind
    candidate_brand_refs: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def require_brand_action(self) -> Self:
        if self.action_kind not in {
            KnowledgeActionKind.CONTENT_WRITE,
            KnowledgeActionKind.CONTENT_REWRITE,
            KnowledgeActionKind.CONTENT_EVALUATE,
        }:
            _raise_contract_error(
                "brand_unresolved_action_invalid",
                "brand resolution is only valid for brand-sensitive content actions",
            )
        return self


type KnowledgePreparation = Annotated[
    KnowledgePreparationReady
    | KnowledgePreparationDegraded
    | RequiredContextPreparationError
    | BrandUnresolvedPreparation,
    Field(discriminator="status"),
]
_PREPARATION_ADAPTER: TypeAdapter[KnowledgePreparation] = TypeAdapter(KnowledgePreparation)


def parse_knowledge_preparation(payload: JsonObject) -> KnowledgePreparation:
    return _PREPARATION_ADAPTER.validate_python(payload)


__all__ = [
    "BrandUnresolvedPreparation",
    "DegradedKnowledgeReason",
    "KnowledgePreparation",
    "KnowledgePreparationDegraded",
    "KnowledgePreparationReady",
    "PreparedContextBlock",
    "PreparedContextRole",
    "PreparedContextSlot",
    "PreparedKnowledgeContext",
    "RequiredContextErrorCode",
    "RequiredContextPreparationError",
    "parse_knowledge_preparation",
]
