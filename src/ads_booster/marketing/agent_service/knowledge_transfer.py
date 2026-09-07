from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRejected,
    ContextTransferValidationRequest,
    EditorialContextBlock,
    EvidenceExcerpt,
    KnowledgeContextTransfer,
)
from ads_booster.contracts.knowledge_context_validation import TrustedKnowledgeContextBinding
from ads_booster.contracts.knowledge_selection import ContextReceipt, ContextRequest


@dataclass(frozen=True, slots=True)
class TransferContextMaterial:
    request: ContextRequest
    receipt: ContextReceipt
    editorial_context: tuple[EditorialContextBlock, ...] = ()
    evidence_excerpts: tuple[EvidenceExcerpt, ...] = ()


type ContextTransferValidationResult = (
    ContextTransferValidationAccepted | ContextTransferValidationRejected
)


class KnowledgeTransferProvider(Protocol):
    def create_transfer(
        self,
        *,
        binding: TrustedKnowledgeContextBinding,
        material: TransferContextMaterial,
        external_sharing_authority_ref: str,
    ) -> KnowledgeContextTransfer: ...

    def validate_transfer(
        self,
        request: ContextTransferValidationRequest,
        *,
        authenticated_tenant_id: str,
        authenticated_principal_id: str,
    ) -> ContextTransferValidationResult: ...

    def record_replica(
        self,
        transfer_id: str,
        system_id: str,
        replica_id: str,
    ) -> None: ...


__all__ = [
    "ContextTransferValidationResult",
    "KnowledgeTransferProvider",
    "TransferContextMaterial",
]
