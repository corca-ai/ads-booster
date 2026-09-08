from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final, Literal, LiteralString, Self

from pydantic import AfterValidator, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.knowledge_selection import (  # noqa: TC001
    ContextReceipt,
    ContextRequest,
    KnowledgeActionKind,
)
from ads_booster.contracts.models import ContractModel, Sha256Digest

_UTC_ERROR_TYPE: Final = "knowledge_timestamp_not_utc"
_UTC_ERROR_MESSAGE: Final = "knowledge context timestamps must use UTC"
_MAX_TRANSFER_LIFETIME: Final = timedelta(hours=24)
_MAX_EDITORIAL_CHARACTERS: Final = 20_000
_MAX_EVIDENCE_CHARACTERS: Final = 12_000


def _raise_contract_error(code: LiteralString, message: LiteralString) -> None:
    raise PydanticCustomError(code, message)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise PydanticCustomError(_UTC_ERROR_TYPE, _UTC_ERROR_MESSAGE)
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
EditorialText = Annotated[str, Field(min_length=1, max_length=4_000)]
EvidenceText = Annotated[str, Field(min_length=1, max_length=1_000)]


class KnowledgeContextModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


@unique
class EditorialContextRole(StrEnum):
    CONSTRAINT = "constraint"
    REFERENCE = "reference"
    EXAMPLE = "example"


class EditorialContextBlock(KnowledgeContextModel):
    block_id: BoundedId
    role: EditorialContextRole
    text: EditorialText
    revision_refs: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=16)]


class EvidenceExcerpt(KnowledgeContextModel):
    source_id: BoundedId
    revision_id: BoundedId
    segment_id: BoundedId
    text: EvidenceText


class KnowledgeContextTransfer(KnowledgeContextModel):
    """Declared transfer metadata; callers must validate it against trusted bindings."""

    schema_version: Literal["trace.knowledge-context.v1"] = Field(alias="schema")
    transfer_id: BoundedId
    workspace_id: BoundedId
    account_id: BoundedId
    scoped_actor_ref: BoundedId
    brand_ref: BoundedId | None = None
    action_kind: KnowledgeActionKind
    run_ref: BoundedId
    task_ref: BoundedId
    invocation_ref: BoundedId
    request: ContextRequest
    receipt: ContextReceipt
    editorial_context: Annotated[tuple[EditorialContextBlock, ...], Field(max_length=32)] = ()
    evidence_excerpts: Annotated[tuple[EvidenceExcerpt, ...], Field(max_length=24)] = ()
    policy_revision: BoundedId
    created_at: UtcDatetime
    expires_at: UtcDatetime

    @model_validator(mode="after")
    def require_internal_bindings(self) -> Self:
        if self.request.task_ref != self.task_ref or self.receipt.task_ref != self.task_ref:
            _raise_contract_error(
                "knowledge_context_task_mismatch",
                "request and receipt tasks must match the transfer task",
            )
        if (
            self.request.action_kind is not self.action_kind
            or self.receipt.action_kind is not self.action_kind
        ):
            _raise_contract_error(
                "knowledge_context_action_mismatch",
                "request and receipt actions must match the transfer action",
            )
        if (
            self.request.brand_ref != self.brand_ref
            or self.receipt.resolved_brand_ref != self.brand_ref
        ):
            _raise_contract_error(
                "knowledge_context_brand_mismatch",
                "request and receipt brands must match the transfer brand",
            )
        if (
            self.receipt.team_id != self.workspace_id
            or self.receipt.scoped_actor_ref != self.scoped_actor_ref
        ):
            _raise_contract_error(
                "knowledge_context_receipt_identity_mismatch",
                "receipt workspace and actor must match the transfer",
            )
        if self.receipt.policy_version != self.policy_revision:
            _raise_contract_error(
                "knowledge_context_policy_mismatch",
                "receipt policy must match the transfer policy",
            )
        return self

    @model_validator(mode="after")
    def require_bounded_lifetime_and_content(self) -> Self:
        if self.created_at < self.receipt.created_at or self.expires_at <= self.created_at:
            _raise_contract_error(
                "knowledge_context_lifetime_invalid",
                "transfer creation and expiry must follow receipt creation",
            )
        if self.expires_at - self.created_at > _MAX_TRANSFER_LIFETIME:
            _raise_contract_error(
                "knowledge_context_lifetime_too_long",
                "knowledge context transfers expire within 24 hours",
            )
        if sum(len(block.text) for block in self.editorial_context) > _MAX_EDITORIAL_CHARACTERS:
            _raise_contract_error(
                "knowledge_context_editorial_text_too_large",
                "combined editorial context exceeds its portable bound",
            )
        if sum(len(excerpt.text) for excerpt in self.evidence_excerpts) > _MAX_EVIDENCE_CHARACTERS:
            _raise_contract_error(
                "knowledge_context_evidence_text_too_large",
                "combined evidence excerpts exceed their portable bound",
            )
        return self

    @model_validator(mode="after")
    def require_selected_excerpt_revisions(self) -> Self:
        selected = {
            (source.source_id, source.revision_id, segment_id)
            for source in self.receipt.selected_source_revisions
            for segment_id in source.segment_ids
        }
        if any(
            (excerpt.source_id, excerpt.revision_id, excerpt.segment_id) not in selected
            for excerpt in self.evidence_excerpts
        ):
            _raise_contract_error(
                "knowledge_context_excerpt_not_selected",
                "every evidence excerpt must refer to a selected immutable source segment",
            )
        selected_revisions = (
            {selected.revision_id for selected in self.receipt.selected_memory_revisions}
            | {selected.revision_id for selected in self.receipt.selected_wiki_claims}
            | {selected.revision_id for selected in self.receipt.selected_source_revisions}
            | {selected.revision_id for selected in self.receipt.required_constraints}
        )
        if self.receipt.soul_revision_id is not None:
            selected_revisions.add(self.receipt.soul_revision_id)
        if any(
            revision not in selected_revisions
            for block in self.editorial_context
            for revision in block.revision_refs
        ):
            _raise_contract_error(
                "knowledge_context_editorial_revision_not_selected",
                "every editorial block must refer to a selected immutable revision",
            )
        return self


@unique
class ValidationStage(StrEnum):
    PRE_DISPATCH = "pre_dispatch"
    ACCEPT_RESULT = "accept_result"


class ContextTransferValidationRequest(KnowledgeContextModel):
    schema_version: Literal["trace.knowledge-context-validation-request.v1"] = Field(alias="schema")
    request_id: BoundedId
    principal_id: BoundedId
    stage: ValidationStage
    transfer_id: BoundedId
    workspace_id: BoundedId
    account_id: BoundedId
    knowledge_context_sha256: Sha256Digest


class ContextTransferValidationAccepted(KnowledgeContextModel):
    schema_version: Literal["trace.knowledge-context-validation-result.v1"] = Field(alias="schema")
    status: Literal["accepted"]
    request_id: BoundedId
    principal_id: BoundedId
    stage: ValidationStage
    transfer_id: BoundedId
    workspace_id: BoundedId
    account_id: BoundedId
    knowledge_context_sha256: Sha256Digest
    dependency_set_sha256: Sha256Digest
    checked_at: UtcDatetime
    valid_until: UtcDatetime

    @model_validator(mode="after")
    def require_ordered_validity(self) -> Self:
        if self.valid_until <= self.checked_at:
            _raise_contract_error(
                "knowledge_context_validation_lifetime_invalid",
                "accepted validation must remain valid after it is checked",
            )
        return self

    def authority_valid_until(self) -> datetime | None:
        return self.valid_until


@unique
class ValidationRejectionCode(StrEnum):
    ACCESS_REVOKED = "access_revoked"
    DEPENDENCY_CHANGED = "dependency_changed"
    EXPIRED = "expired"
    OWNER_UNAVAILABLE = "owner_unavailable"
    TOMBSTONED = "tombstoned"
    TRANSFER_BLOCKED = "transfer_blocked"


class ContextTransferValidationRejected(KnowledgeContextModel):
    schema_version: Literal["trace.knowledge-context-validation-result.v1"] = Field(alias="schema")
    status: Literal["rejected"]
    request_id: BoundedId
    principal_id: BoundedId
    stage: ValidationStage
    transfer_id: BoundedId
    workspace_id: BoundedId
    account_id: BoundedId
    knowledge_context_sha256: Sha256Digest
    rejection_code: ValidationRejectionCode
    checked_at: UtcDatetime

    def authority_valid_until(self) -> datetime | None:
        return None


class KnowledgeContextUseReceipt(KnowledgeContextModel):
    schema_version: Literal["trace.knowledge-context-use-receipt.v1"] = Field(alias="schema")
    transfer_id: BoundedId
    knowledge_context_sha256: Sha256Digest
    context_receipt: ContextReceipt


def knowledge_context_sha256(transfer: KnowledgeContextTransfer) -> str:
    return contract_sha256(transfer)


__all__ = [
    "ContextTransferValidationAccepted",
    "ContextTransferValidationRejected",
    "ContextTransferValidationRequest",
    "EditorialContextBlock",
    "EditorialContextRole",
    "EvidenceExcerpt",
    "KnowledgeContextTransfer",
    "KnowledgeContextUseReceipt",
    "ValidationRejectionCode",
    "ValidationStage",
    "knowledge_context_sha256",
]
