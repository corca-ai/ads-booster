"""Immutable procedural-skill contracts owned by the knowledge repository."""

# ruff: noqa: TC001
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_memory import LegacyMemoryAssessment
from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import (
    BoundedReason,
    BoundedText,
    KnowledgeContractModel,
    UtcDatetime,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.operation_contracts import OperationReceipt
from ads_booster.knowledge.operation_enums import (
    SkillOperationKind,
    SkillOrigin,
    SkillTargetKind,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class SkillRecord(KnowledgeContractModel):
    """One content-addressed procedural-skill revision."""

    schema_version: Literal["knowledge.skill-record.v1"] = Field(alias="schema")
    skill_id: BoundedId
    version: BoundedId
    description: BoundedText
    procedure: BoundedText
    pitfalls: Annotated[tuple[BoundedText, ...], Field(max_length=32)] = ()
    verification: Annotated[tuple[BoundedText, ...], Field(max_length=32)] = ()
    required_capability_ids: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()
    origin: SkillOrigin
    protected: bool
    base_builtin_digest: Sha256Digest | None = None
    digest: Sha256Digest
    applicability: AppliesTo
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()
    authority_ref: AuthorityRef | None = None
    created_by: BoundedId
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def require_origin_shape(self) -> Self:
        match self.origin:
            case SkillOrigin.BUILTIN:
                valid = self.protected and self.base_builtin_digest is None
            case SkillOrigin.BUILTIN_OVERRIDE:
                valid = (
                    self.protected
                    and self.base_builtin_digest is not None
                    and bool(self.source_refs)
                )
            case SkillOrigin.AGENT_CREATED:
                valid = (
                    not self.protected
                    and self.base_builtin_digest is None
                    and bool(self.source_refs)
                )
        if not valid:
            code = "skill_record_origin_invalid"
            raise PydanticCustomError(
                code,
                "skill origin must match its protection, builtin binding, and source references",
            )
        return self

    @model_validator(mode="after")
    def require_content_digest(self) -> Self:
        if contract_sha256(self.stable_content()) != self.digest:
            code = "skill_record_digest_mismatch"
            raise PydanticCustomError(
                code,
                "skill digest must bind its stable content",
            )
        return self

    def stable_content(self) -> JsonObject:
        """Return the canonical skill content covered by ``digest``."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "procedure": self.procedure,
            "pitfalls": list(self.pitfalls),
            "verification": list(self.verification),
            "required_capability_ids": list(self.required_capability_ids),
            "origin": self.origin.value,
            "protected": self.protected,
            "base_builtin_digest": self.base_builtin_digest,
            "source_refs": [item.model_dump(mode="json") for item in self.source_refs],
            "applicability": self.applicability.model_dump(mode="json", exclude_defaults=True),
            "created_by": self.created_by,
        }


class SkillOperation(KnowledgeContractModel):
    """A source-bound compare-and-swap operation on one skill revision."""

    operation_id: BoundedId
    kind: SkillOperationKind
    skill_id: BoundedId
    expected_revision_id: BoundedId | None = None
    replacement_revision_id: BoundedId | None = None
    record: SkillRecord | None = None
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1, max_length=128)]
    reason: BoundedReason

    @model_validator(mode="after")
    def require_kind_bindings(self) -> Self:
        record_matches = (
            self.record is not None
            and self.record.skill_id == self.skill_id
            and self.record.version == self.replacement_revision_id
            and self.record.source_refs == self.source_refs
        )
        match self.kind:
            case SkillOperationKind.CREATE:
                valid = self.expected_revision_id is None and record_matches
            case SkillOperationKind.UPDATE | SkillOperationKind.SUPERSEDE:
                valid = self.expected_revision_id is not None and record_matches
            case SkillOperationKind.RETRACT:
                valid = (
                    self.expected_revision_id is not None
                    and self.replacement_revision_id is None
                    and self.record is None
                )
        if not valid:
            code = "skill_operation_binding_invalid"
            raise PydanticCustomError(
                code,
                "skill operation fields must match its kind and target revision",
            )
        return self


class SkillMutationDraft(KnowledgeContractModel):
    """Model-authored semantic content without server-owned persistence metadata."""

    description: BoundedText
    procedure: BoundedText
    pitfalls: Annotated[tuple[BoundedText, ...], Field(max_length=32)] = ()
    verification: Annotated[tuple[BoundedText, ...], Field(max_length=32)] = ()
    required_capability_ids: Annotated[tuple[BoundedId, ...], Field(max_length=64)] | None = None
    applicability: AppliesTo | None = None


class SkillDraftOperation(KnowledgeContractModel):
    """Wire mutation normalized to a strict SkillOperation at the trusted boundary."""

    operation_id: BoundedId | None = None
    kind: SkillOperationKind
    skill_id: BoundedId
    expected_revision_id: BoundedId | None = None
    replacement_revision_id: BoundedId | None = None
    draft: SkillMutationDraft | None = None
    reason: BoundedReason

    @model_validator(mode="after")
    def require_kind_bindings(self) -> Self:
        match self.kind:
            case SkillOperationKind.CREATE:
                valid = self.expected_revision_id is None and self.draft is not None
            case SkillOperationKind.UPDATE | SkillOperationKind.SUPERSEDE:
                valid = self.expected_revision_id is not None and self.draft is not None
            case SkillOperationKind.RETRACT:
                valid = (
                    self.expected_revision_id is not None
                    and self.replacement_revision_id is None
                    and self.draft is None
                )
        if not valid:
            code = "skill_draft_operation_binding_invalid"
            raise PydanticCustomError(
                code,
                "skill draft fields must match its mutation kind and revision bindings",
            )
        return self


type SkillApplyOperation = SkillDraftOperation | SkillOperation


class SkillListInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.skill-list.v1"] = Field(alias="schema")
    applicability: AppliesTo | None = None
    include_protected: bool = True
    query: Annotated[str, Field(max_length=2000)] = ""
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class SkillGetInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.skill-get.v1"] = Field(alias="schema")
    skill_id: BoundedId
    revision_id: BoundedId | None = None


class SkillApplyInput(KnowledgeContractModel):
    schema_version: Literal["knowledge.tool.skill-apply.v1"] = Field(alias="schema")
    operation_id: BoundedId
    operations: Annotated[
        tuple[SkillApplyOperation, ...],
        Field(
            min_length=1,
            max_length=20,
            description=(
                "Model-authored calls use SkillDraftOperation; the server derives provenance, "
                "creator, timestamps, origin, protection, and digest. SkillOperation is the "
                "strict complete-record compatibility path."
            ),
        ),
    ]
    legacy_memory_assessments: Annotated[
        tuple[LegacyMemoryAssessment, ...], Field(max_length=6)
    ] = ()

    @model_validator(mode="after")
    def require_operation_binding(self) -> Self:
        for operation in self.operations:
            match operation:
                case SkillOperation() if operation.operation_id != self.operation_id:
                    code = "skill_apply_operation_mismatch"
                    raise PydanticCustomError(
                        code,
                        "every strict skill operation must match the request operation",
                    )
                case SkillOperation() | SkillDraftOperation():
                    continue
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> JsonObject:
        result = _JSON_OBJECT.validate_python(handler(self))
        if not self.legacy_memory_assessments:
            _ = result.pop("legacy_memory_assessments", None)
        return result


class SkillReference(KnowledgeContractModel):
    """Stable identity of one effective procedural-skill revision."""

    skill_id: BoundedId
    revision_id: BoundedId
    content_sha256: Sha256Digest
    origin: SkillOrigin
    protected: bool
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()


class SkillCatalogEntry(KnowledgeContractModel):
    """Body-free metadata suitable for context selection."""

    reference: SkillReference
    description: BoundedText
    required_capability_ids: Annotated[tuple[BoundedId, ...], Field(max_length=64)] = ()
    applicability: AppliesTo
    override_status: Literal["current", "base_release_mismatch", "source_stale"] | None = None


class SkillListData(KnowledgeContractModel):
    kind: Literal["skill_list"] = "skill_list"
    entries: Annotated[tuple[SkillCatalogEntry, ...], Field(max_length=512)]
    total_matches: Annotated[int, Field(ge=0)] = 0
    next_offset: Annotated[int, Field(ge=0)] | None = None


class SkillData(KnowledgeContractModel):
    kind: Literal["skill"] = "skill"
    record: SkillRecord
    markdown: Annotated[str, Field(max_length=200_000)]
    display_pending: bool = False
    effective: bool = True
    override_status: Literal["current", "base_release_mismatch", "source_stale"] | None = None


class SkillApplyData(KnowledgeContractModel):
    kind: Literal["skill_apply"] = "skill_apply"
    operation_id: BoundedId
    target_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=20)]
    receipt: OperationReceipt


__all__ = [
    "SkillApplyData",
    "SkillApplyInput",
    "SkillCatalogEntry",
    "SkillData",
    "SkillDraftOperation",
    "SkillGetInput",
    "SkillListData",
    "SkillListInput",
    "SkillMutationDraft",
    "SkillOperation",
    "SkillOperationKind",
    "SkillOrigin",
    "SkillRecord",
    "SkillReference",
    "SkillTargetKind",
]
