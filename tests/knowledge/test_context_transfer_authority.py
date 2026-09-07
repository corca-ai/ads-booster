from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRequest,
    KnowledgeContextUseReceipt,
    ValidationStage,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_context_validation import (
    KnowledgeContextContractError,
    KnowledgeContextErrorCode,
    validate_callback_context_receipt,
    validate_context_transfer_authority,
)
from tests.knowledge.transfer_contract_fixtures import DIGEST_A, NOW, transfer_fixture

if TYPE_CHECKING:
    from collections.abc import Callable


def _assert_code[T](expected: KnowledgeContextErrorCode, action: Callable[[], T]) -> None:
    with pytest.raises(KnowledgeContextContractError) as caught:
        _ = action()
    assert caught.value.code is expected


def test_validation_result_and_callback_must_match_exact_transfer_receipt() -> None:
    # Given
    transfer = transfer_fixture()
    digest = knowledge_context_sha256(transfer)
    request = ContextTransferValidationRequest(
        schema="trace.knowledge-context-validation-request.v1",
        request_id="validation.1",
        principal_id="hosted.service.1",
        stage=ValidationStage.PRE_DISPATCH,
        transfer_id=transfer.transfer_id,
        workspace_id=transfer.workspace_id,
        account_id=transfer.account_id,
        knowledge_context_sha256=digest,
    )
    accepted = ContextTransferValidationAccepted(
        schema="trace.knowledge-context-validation-result.v1",
        status="accepted",
        request_id=request.request_id,
        principal_id=request.principal_id,
        stage=request.stage,
        transfer_id=request.transfer_id,
        workspace_id=request.workspace_id,
        account_id=request.account_id,
        knowledge_context_sha256=digest,
        dependency_set_sha256=DIGEST_A,
        checked_at=NOW,
        valid_until=NOW + timedelta(minutes=1),
    )
    used = KnowledgeContextUseReceipt(
        schema="trace.knowledge-context-use-receipt.v1",
        transfer_id=transfer.transfer_id,
        knowledge_context_sha256=digest,
        context_receipt=transfer.receipt,
    )

    # When / Then
    validate_context_transfer_authority(request, accepted, now=NOW)
    validate_callback_context_receipt(transfer, digest, used)

    tampered = used.model_copy(
        update={"context_receipt": transfer.receipt.model_copy(update={"receipt_id": "receipt.2"})}
    )
    _assert_code(
        KnowledgeContextErrorCode.CALLBACK_RECEIPT_MISMATCH,
        lambda: validate_callback_context_receipt(transfer, digest, tampered),
    )


@pytest.mark.parametrize("stage", list(ValidationStage))
def test_both_authority_validation_stages_reject_misbound_results(stage: ValidationStage) -> None:
    # Given
    transfer = transfer_fixture()
    digest = knowledge_context_sha256(transfer)
    request = ContextTransferValidationRequest(
        schema="trace.knowledge-context-validation-request.v1",
        request_id=f"validation.{stage.value}",
        principal_id="hosted.service.1",
        stage=stage,
        transfer_id=transfer.transfer_id,
        workspace_id=transfer.workspace_id,
        account_id=transfer.account_id,
        knowledge_context_sha256=digest,
    )
    accepted = ContextTransferValidationAccepted(
        schema="trace.knowledge-context-validation-result.v1",
        status="accepted",
        request_id=request.request_id,
        principal_id=request.principal_id,
        stage=stage,
        transfer_id=request.transfer_id,
        workspace_id=request.workspace_id,
        account_id=request.account_id,
        knowledge_context_sha256=digest,
        dependency_set_sha256=DIGEST_A,
        checked_at=NOW,
        valid_until=NOW + timedelta(minutes=1),
    )

    # When / Then
    validate_context_transfer_authority(request, accepted, now=NOW)
    misbound = accepted.model_copy(update={"account_id": "account.b"})
    _assert_code(
        KnowledgeContextErrorCode.VALIDATION_RESULT_MISMATCH,
        lambda: validate_context_transfer_authority(request, misbound, now=NOW),
    )
