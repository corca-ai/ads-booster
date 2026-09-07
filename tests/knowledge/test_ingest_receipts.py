from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import ConversationEventKind
from ads_booster.knowledge.ingest_receipts import (
    IngestDeliveryReceipt,
    IngestDeliveryReceiptValidationError,
    IngestUnitKind,
    IngestUnitReceipt,
    ingest_unit_delivery_id,
    validate_ingest_delivery_receipt,
)
from ads_booster.knowledge.source_contracts import (
    AttachmentCapability,
    IngestEnvelope,
    IngestReceipt,
    MessageEventRef,
)

NOW = datetime(2026, 9, 7, 14, tzinfo=UTC)


def _envelope(
    kind: ConversationEventKind = ConversationEventKind.MESSAGE_FINALIZED,
    ordinals: tuple[int, ...] = (),
) -> IngestEnvelope:
    attachment_only = kind is ConversationEventKind.ATTACHMENT_RECEIVED
    return IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="parent.delivery.1",
        event_kind=kind,
        request_text=""
        if attachment_only or kind is ConversationEventKind.MESSAGE_DELETED
        else "Approved price is KRW 31,000.",
        message_event=None
        if attachment_only
        else MessageEventRef(
            conversation_ref="conversation.1", message_ref="message.1", revision=1
        ),
        attachments=tuple(
            AttachmentCapability(
                ordinal=ordinal,
                logical_source_ref=f"attachment.source.{ordinal}",
                logical_revision_ref=f"attachment.source.{ordinal}.revision.1",
                mime_type="application/pdf",
            )
            for ordinal in ordinals
        ),
        timestamp=NOW,
    )


def _unit(parent: str, kind: IngestUnitKind, ordinal: int | None) -> IngestUnitReceipt:
    identity = "message" if ordinal is None else f"attachment.{ordinal}"
    return IngestUnitReceipt(
        kind=kind,
        ordinal=ordinal,
        receipt=IngestReceipt(
            schema="knowledge.ingest-receipt.v1",
            delivery_id=ingest_unit_delivery_id(parent, kind, ordinal),
            source_id=f"source.{identity}",
            source_revision_id=f"source.{identity}.revision.1",
            curation_job_id=f"curation.{identity}",
            index_operation_id=f"index.{identity}",
            replayed=False,
        ),
    )


def _receipt(envelope: IngestEnvelope) -> IngestDeliveryReceipt:
    message_units = (
        ()
        if envelope.event_kind is ConversationEventKind.ATTACHMENT_RECEIVED
        else (_unit(envelope.delivery_id, IngestUnitKind.MESSAGE, None),)
    )
    attachment_units = tuple(
        _unit(envelope.delivery_id, IngestUnitKind.ATTACHMENT, attachment.ordinal)
        for attachment in envelope.attachments
    )
    return IngestDeliveryReceipt(
        delivery_id=envelope.delivery_id,
        envelope_sha256=contract_sha256(envelope),
        unit_receipts=(*message_units, *attachment_units),
    )


@pytest.mark.parametrize(
    ("kind", "ordinals", "expected"),
    [
        (ConversationEventKind.MESSAGE_FINALIZED, (), (("message", None),)),
        (
            ConversationEventKind.MESSAGE_FINALIZED,
            (2, 7),
            (("message", None), ("attachment", 2), ("attachment", 7)),
        ),
        (
            ConversationEventKind.ATTACHMENT_RECEIVED,
            (2, 7),
            (("attachment", 2), ("attachment", 7)),
        ),
        (ConversationEventKind.MESSAGE_DELETED, (), (("message", None),)),
    ],
)
def test_delivery_receipt_accepts_every_complete_envelope_unit_set(
    kind: ConversationEventKind,
    ordinals: tuple[int, ...],
    expected: tuple[tuple[str, int | None], ...],
) -> None:
    envelope = _envelope(kind, ordinals)
    receipt = _receipt(envelope)

    validate_ingest_delivery_receipt(envelope, receipt)

    assert tuple((unit.kind.value, unit.ordinal) for unit in receipt.unit_receipts) == expected
    assert receipt.model_dump(by_alias=True)["schema"] == "knowledge.ingest-delivery-receipt.v1"


def test_delivery_receipt_rejects_parent_digest_and_incomplete_unit_sets() -> None:
    envelope = _envelope(ordinals=(2,))
    receipt = _receipt(envelope)
    cases = (
        (
            receipt.model_copy(update={"delivery_id": "parent.delivery.forged"}),
            "ingest_delivery_receipt_parent_delivery_mismatch",
        ),
        (
            receipt.model_copy(update={"envelope_sha256": "f" * 64}),
            "ingest_delivery_receipt_envelope_sha256_mismatch",
        ),
        (
            receipt.model_copy(update={"unit_receipts": receipt.unit_receipts[:1]}),
            "ingest_delivery_receipt_units_missing",
        ),
        (
            receipt.model_copy(
                update={
                    "unit_receipts": (
                        *receipt.unit_receipts,
                        _unit(envelope.delivery_id, IngestUnitKind.ATTACHMENT, 7),
                    )
                }
            ),
            "ingest_delivery_receipt_units_extraneous",
        ),
    )
    for invalid, code in cases:
        with pytest.raises(IngestDeliveryReceiptValidationError, match=code):
            validate_ingest_delivery_receipt(envelope, invalid)


def _duplicate_unit(receipt: IngestDeliveryReceipt) -> IngestDeliveryReceipt:
    return receipt.model_copy(
        update={"unit_receipts": (*receipt.unit_receipts[:2], receipt.unit_receipts[1])}
    )


def _noncanonical(receipt: IngestDeliveryReceipt) -> IngestDeliveryReceipt:
    return receipt.model_copy(
        update={
            "unit_receipts": (
                receipt.unit_receipts[1],
                receipt.unit_receipts[0],
                receipt.unit_receipts[2],
            )
        }
    )


def _substitute_child(
    receipt: IngestDeliveryReceipt,
    delivery_id: str,
) -> IngestDeliveryReceipt:
    units = receipt.unit_receipts
    changed = units[1].model_copy(
        update={"receipt": units[1].receipt.model_copy(update={"delivery_id": delivery_id})}
    )
    return receipt.model_copy(update={"unit_receipts": (units[0], changed, units[2])})


def test_delivery_receipt_rejects_noncanonical_or_substituted_children() -> None:
    envelope = _envelope(ordinals=(2, 7))
    receipt = _receipt(envelope)
    cases = (
        (_duplicate_unit(receipt), "ingest_delivery_receipt_duplicate_units"),
        (_noncanonical(receipt), "ingest_delivery_receipt_units_not_canonical"),
        (
            _substitute_child(receipt, receipt.unit_receipts[0].receipt.delivery_id),
            "ingest_delivery_receipt_duplicate_child_delivery_ids",
        ),
        (
            _substitute_child(receipt, "substituted.child.delivery"),
            "ingest_delivery_receipt_child_delivery_binding_mismatch",
        ),
    )
    for invalid, code in cases:
        with pytest.raises(ValidationError, match=code):
            validate_ingest_delivery_receipt(envelope, invalid)


def test_delivery_receipt_rejects_wrong_attachment_ordinal_and_is_frozen() -> None:
    envelope = _envelope(ordinals=(2,))
    receipt = _receipt(envelope)
    wrong_ordinal = receipt.model_copy(
        update={
            "unit_receipts": (
                receipt.unit_receipts[0],
                _unit(envelope.delivery_id, IngestUnitKind.ATTACHMENT, 7),
            )
        }
    )

    with pytest.raises(
        IngestDeliveryReceiptValidationError,
        match="ingest_delivery_receipt_units_missing",
    ):
        validate_ingest_delivery_receipt(envelope, wrong_ordinal)
    with pytest.raises(ValidationError, match="frozen_instance"):
        receipt.delivery_id = "mutated"


def test_unit_delivery_ids_are_stable_and_bounded() -> None:
    message_id = ingest_unit_delivery_id("parent.delivery.1", IngestUnitKind.MESSAGE, None)
    attachment_id = ingest_unit_delivery_id("parent.delivery.1", IngestUnitKind.ATTACHMENT, 255)

    assert message_id == ingest_unit_delivery_id("parent.delivery.1", IngestUnitKind.MESSAGE, None)
    assert message_id != attachment_id
    assert len(message_id) <= 160
