from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING, Protocol

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import MemoryKind

if TYPE_CHECKING:
    from ads_booster.knowledge.adoption_contracts import AdoptionReceiptResolver
    from ads_booster.knowledge.memory import MemorySnapshot
    from ads_booster.knowledge.scope_contracts import ActorContext


class MemoryPublicationLike(Protocol):
    @property
    def snapshot(self) -> MemorySnapshot: ...


def require_soul_adoptions(
    *,
    actor: ActorContext,
    memories: tuple[MemoryPublicationLike, ...],
    receipt_ids: tuple[str, ...],
    resolver: AdoptionReceiptResolver | None,
) -> None:
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ChangeValidationError("soul_adoption_receipts_not_unique")
    if resolver is None:
        receipts = ()
    else:
        receipts = tuple(resolver.adoption_receipt(actor, receipt_id) for receipt_id in receipt_ids)
        missing = next(
            (
                receipt_id
                for receipt_id, receipt in zip(receipt_ids, receipts, strict=True)
                if receipt is None
            ),
            None,
        )
        if missing is not None:
            raise ChangeValidationError("soul_adoption_receipt_missing", missing)
    used: set[str] = set()
    for publication in memories:
        snapshot = publication.snapshot
        if snapshot.document.kind is not MemoryKind.SOUL:
            continue
        brand_id = snapshot.document.brand_id
        expected_revision_id = snapshot.revision.previous_revision_id
        matches = tuple(
            receipt
            for receipt in receipts
            if receipt is not None
            if receipt.workspace_id == actor.workspace_id
            and receipt.actor_ref == actor.actor_id
            and receipt.brand_id == brand_id
            and receipt.expected_revision_id == expected_revision_id
        )
        if len(matches) != 1:
            raise ChangeValidationError(
                "soul_explicit_adoption_required", snapshot.document.document_id
            )
        receipt = matches[0]
        authority_event_ids = {
            entry.authority_ref.event_id
            for entry in snapshot.entries
            if entry.authority_ref is not None
        }
        if receipt.authenticated_event_id not in authority_event_ids:
            raise ChangeValidationError(
                "soul_adoption_event_mismatch", snapshot.document.document_id
            )
        if receipt.receipt_id in used:
            raise ChangeValidationError("soul_adoption_receipt_reused", receipt.receipt_id)
        used.add(receipt.receipt_id)
    if len(used) != len(receipt_ids):
        raise ChangeValidationError("unused_soul_adoption_receipt")


__all__ = ["require_soul_adoptions"]
