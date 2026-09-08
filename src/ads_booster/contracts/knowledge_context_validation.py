from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, Literal, override

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRejected,
    ContextTransferValidationRequest,
    KnowledgeContextTransfer,
    KnowledgeContextUseReceipt,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind  # noqa: TC001
from ads_booster.contracts.models import Sha256Digest  # noqa: TC001
from ads_booster.transport.json_types import JsonObject  # noqa: TC001

if TYPE_CHECKING:
    from datetime import datetime


@unique
class KnowledgeContextErrorCode(StrEnum):
    REQUIRED_CONTEXT_MISSING = "required_context_missing"
    FIELD_PAIR_INCOMPLETE = "context_field_pair_incomplete"
    DISABLED_CONTEXT_PRESENT = "disabled_context_present"
    INVALID_CONTEXT = "invalid_context"
    DIGEST_MISMATCH = "context_digest_mismatch"
    EXPIRED = "context_expired"
    WORKSPACE_MISMATCH = "context_workspace_mismatch"
    ACCOUNT_MISMATCH = "context_account_mismatch"
    ACTOR_MISMATCH = "context_actor_mismatch"
    BRAND_MISMATCH = "context_brand_mismatch"
    ACTION_MISMATCH = "context_action_mismatch"
    RUN_MISMATCH = "context_run_mismatch"
    TASK_MISMATCH = "context_task_mismatch"
    INVOCATION_MISMATCH = "context_invocation_mismatch"
    VALIDATION_RESULT_MISMATCH = "context_validation_result_mismatch"
    AUTHORITY_REJECTED = "context_authority_rejected"
    VALIDATION_EXPIRED = "context_validation_expired"
    CALLBACK_RECEIPT_MISMATCH = "callback_context_receipt_mismatch"


@dataclass(slots=True)
class KnowledgeContextContractError(Exception):
    code: KnowledgeContextErrorCode

    @override
    def __str__(self) -> str:
        """Return the stable machine-readable failure code."""
        return self.code.value


@dataclass(frozen=True, slots=True)
class TrustedKnowledgeContextBinding:
    workspace_id: BoundedId
    account_id: BoundedId
    scoped_actor_ref: BoundedId
    brand_ref: BoundedId | None
    action_kind: KnowledgeActionKind
    run_ref: BoundedId
    task_ref: BoundedId
    invocation_ref: BoundedId


@dataclass(frozen=True, slots=True)
class TrustedKnowledgeRequired:
    binding: TrustedKnowledgeContextBinding


@dataclass(frozen=True, slots=True)
class TrustedKnowledgeDisabled:
    binding: None = dataclass_field(default=None, init=False)


type TrustedKnowledgePolicy = TrustedKnowledgeRequired | TrustedKnowledgeDisabled


@dataclass(frozen=True, slots=True)
class KnowledgeContextAbsent:
    reason: Literal["trusted_knowledge_disabled"] = "trusted_knowledge_disabled"


type ParsedTaskKnowledgeContext = KnowledgeContextTransfer | KnowledgeContextAbsent
type ContextTransferValidationResult = Annotated[
    ContextTransferValidationAccepted | ContextTransferValidationRejected,
    Field(discriminator="status"),
]
_VALIDATION_RESULT_ADAPTER: TypeAdapter[ContextTransferValidationResult] = TypeAdapter(
    ContextTransferValidationResult
)


def parse_knowledge_context_fields(
    envelope: JsonObject | None,
    digest: Sha256Digest | None,
    policy: TrustedKnowledgePolicy,
    *,
    now: datetime,
) -> ParsedTaskKnowledgeContext:
    has_envelope = envelope is not None
    has_digest = digest is not None
    if has_envelope != has_digest:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.FIELD_PAIR_INCOMPLETE)
    binding = policy.binding
    if binding is None:
        if has_envelope:
            raise KnowledgeContextContractError(KnowledgeContextErrorCode.DISABLED_CONTEXT_PRESENT)
        return KnowledgeContextAbsent()
    if envelope is None or digest is None:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.REQUIRED_CONTEXT_MISSING)
    try:
        transfer = KnowledgeContextTransfer.model_validate(envelope)
    except ValidationError as error:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.INVALID_CONTEXT) from error
    if knowledge_context_sha256(transfer) != digest:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.DIGEST_MISMATCH)
    if now >= transfer.expires_at:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.EXPIRED)
    _validate_trusted_binding(transfer, binding)
    return transfer


def _validate_trusted_binding(
    transfer: KnowledgeContextTransfer,
    binding: TrustedKnowledgeContextBinding,
) -> None:
    comparisons = (
        (
            transfer.workspace_id == binding.workspace_id,
            KnowledgeContextErrorCode.WORKSPACE_MISMATCH,
        ),
        (transfer.account_id == binding.account_id, KnowledgeContextErrorCode.ACCOUNT_MISMATCH),
        (
            transfer.scoped_actor_ref == binding.scoped_actor_ref,
            KnowledgeContextErrorCode.ACTOR_MISMATCH,
        ),
        (transfer.brand_ref == binding.brand_ref, KnowledgeContextErrorCode.BRAND_MISMATCH),
        (transfer.action_kind is binding.action_kind, KnowledgeContextErrorCode.ACTION_MISMATCH),
        (transfer.run_ref == binding.run_ref, KnowledgeContextErrorCode.RUN_MISMATCH),
        (transfer.task_ref == binding.task_ref, KnowledgeContextErrorCode.TASK_MISMATCH),
        (
            transfer.invocation_ref == binding.invocation_ref,
            KnowledgeContextErrorCode.INVOCATION_MISMATCH,
        ),
    )
    for matches, code in comparisons:
        if not matches:
            raise KnowledgeContextContractError(code)


def parse_context_transfer_validation_result(
    payload: JsonObject,
) -> ContextTransferValidationResult:
    return _VALIDATION_RESULT_ADAPTER.validate_python(payload)


def validate_context_transfer_authority(
    request: ContextTransferValidationRequest,
    result: ContextTransferValidationResult,
    *,
    now: datetime,
) -> None:
    request_binding = (
        request.request_id,
        request.principal_id,
        request.stage,
        request.transfer_id,
        request.workspace_id,
        request.account_id,
        request.knowledge_context_sha256,
    )
    result_binding = (
        result.request_id,
        result.principal_id,
        result.stage,
        result.transfer_id,
        result.workspace_id,
        result.account_id,
        result.knowledge_context_sha256,
    )
    if result_binding != request_binding:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.VALIDATION_RESULT_MISMATCH)
    valid_until = result.authority_valid_until()
    if valid_until is None:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.AUTHORITY_REJECTED)
    if now >= valid_until:
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.VALIDATION_EXPIRED)


def validate_callback_context_receipt(
    transfer: KnowledgeContextTransfer,
    expected_digest: Sha256Digest,
    used: KnowledgeContextUseReceipt,
) -> None:
    if (
        used.transfer_id != transfer.transfer_id
        or used.knowledge_context_sha256 != expected_digest
        or used.context_receipt != transfer.receipt
    ):
        raise KnowledgeContextContractError(KnowledgeContextErrorCode.CALLBACK_RECEIPT_MISMATCH)


__all__ = [
    "ContextTransferValidationResult",
    "KnowledgeContextAbsent",
    "KnowledgeContextContractError",
    "KnowledgeContextErrorCode",
    "ParsedTaskKnowledgeContext",
    "TrustedKnowledgeContextBinding",
    "TrustedKnowledgeDisabled",
    "TrustedKnowledgePolicy",
    "TrustedKnowledgeRequired",
    "parse_context_transfer_validation_result",
    "parse_knowledge_context_fields",
    "validate_callback_context_receipt",
    "validate_context_transfer_authority",
]
