from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Self, override

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import Sha256Digest  # noqa: TC001
from ads_booster.knowledge.contract_types import ConversationEventKind, KnowledgeContractModel
from ads_booster.knowledge.source_contracts import IngestEnvelope, IngestReceipt  # noqa: TC001

_MESSAGE_ORDINAL_FORBIDDEN = "ingest_unit_message_ordinal_forbidden"
_MESSAGE_ORDINAL_FORBIDDEN_MESSAGE = "message units cannot have an ordinal"
_ATTACHMENT_ORDINAL_REQUIRED = "ingest_unit_attachment_ordinal_required"
_ATTACHMENT_ORDINAL_REQUIRED_MESSAGE = "attachment units require an ordinal"
_DUPLICATE_UNITS = "ingest_delivery_receipt_duplicate_units"
_DUPLICATE_UNITS_MESSAGE = "delivery receipt units must be unique"
_DUPLICATE_CHILD_DELIVERY_IDS = "ingest_delivery_receipt_duplicate_child_delivery_ids"
_DUPLICATE_CHILD_DELIVERY_IDS_MESSAGE = "delivery receipt child delivery IDs must be unique"
_UNITS_NOT_CANONICAL = "ingest_delivery_receipt_units_not_canonical"
_UNITS_NOT_CANONICAL_MESSAGE = (
    "delivery receipt units must order message before ascending attachments"
)
_CHILD_DELIVERY_BINDING_MISMATCH = "ingest_delivery_receipt_child_delivery_binding_mismatch"
_CHILD_DELIVERY_BINDING_MISMATCH_MESSAGE = (
    "delivery receipt child delivery ID must derive from its parent and unit"
)


class IngestUnitKind(StrEnum):
    MESSAGE = "message"
    ATTACHMENT = "attachment"


@dataclass(slots=True)
class IngestDeliveryReceiptValidationError(ValueError):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


class _IngestUnitDeliveryKey(KnowledgeContractModel):
    schema_version: Literal["knowledge.ingest-unit-delivery-id.v1"] = (
        "knowledge.ingest-unit-delivery-id.v1"
    )
    parent_delivery_id: BoundedId
    kind: IngestUnitKind
    ordinal: Annotated[int, Field(ge=0, le=255)] | None

    @model_validator(mode="after")
    def require_kind_ordinal_shape(self) -> Self:
        match self.kind:
            case IngestUnitKind.MESSAGE:
                if self.ordinal is not None:
                    raise PydanticCustomError(
                        _MESSAGE_ORDINAL_FORBIDDEN,
                        _MESSAGE_ORDINAL_FORBIDDEN_MESSAGE,
                    )
            case IngestUnitKind.ATTACHMENT:
                if self.ordinal is None:
                    raise PydanticCustomError(
                        _ATTACHMENT_ORDINAL_REQUIRED,
                        _ATTACHMENT_ORDINAL_REQUIRED_MESSAGE,
                    )
        return self


def ingest_unit_delivery_id(
    parent_delivery_id: BoundedId,
    kind: IngestUnitKind,
    ordinal: Annotated[int, Field(ge=0, le=255)] | None,
) -> BoundedId:
    key = _IngestUnitDeliveryKey(
        parent_delivery_id=parent_delivery_id,
        kind=kind,
        ordinal=ordinal,
    )
    return "ingest-unit-" + contract_sha256(key)


class IngestUnitReceipt(KnowledgeContractModel):
    kind: IngestUnitKind
    ordinal: Annotated[int, Field(ge=0, le=255)] | None = None
    receipt: IngestReceipt

    @model_validator(mode="after")
    def require_kind_ordinal_shape(self) -> Self:
        _ = _IngestUnitDeliveryKey(
            parent_delivery_id=self.receipt.delivery_id,
            kind=self.kind,
            ordinal=self.ordinal,
        )
        return self


class IngestDeliveryReceipt(KnowledgeContractModel):
    schema_version: Literal["knowledge.ingest-delivery-receipt.v1"] = Field(
        default="knowledge.ingest-delivery-receipt.v1",
        alias="schema",
    )
    delivery_id: BoundedId
    envelope_sha256: Sha256Digest
    unit_receipts: Annotated[tuple[IngestUnitReceipt, ...], Field(min_length=1, max_length=257)]

    @model_validator(mode="after")
    def require_canonical_unit_receipts(self) -> Self:
        unit_keys = tuple((unit.kind, unit.ordinal) for unit in self.unit_receipts)
        if len(unit_keys) != len(set(unit_keys)):
            raise PydanticCustomError(
                _DUPLICATE_UNITS,
                _DUPLICATE_UNITS_MESSAGE,
            )
        child_delivery_ids = tuple(unit.receipt.delivery_id for unit in self.unit_receipts)
        if len(child_delivery_ids) != len(set(child_delivery_ids)):
            raise PydanticCustomError(
                _DUPLICATE_CHILD_DELIVERY_IDS,
                _DUPLICATE_CHILD_DELIVERY_IDS_MESSAGE,
            )
        canonical_units = tuple(
            sorted(
                self.unit_receipts,
                key=lambda unit: (
                    0 if unit.kind is IngestUnitKind.MESSAGE else 1,
                    -1 if unit.ordinal is None else unit.ordinal,
                ),
            )
        )
        if self.unit_receipts != canonical_units:
            raise PydanticCustomError(
                _UNITS_NOT_CANONICAL,
                _UNITS_NOT_CANONICAL_MESSAGE,
            )
        for unit in self.unit_receipts:
            if unit.receipt.delivery_id != ingest_unit_delivery_id(
                self.delivery_id,
                unit.kind,
                unit.ordinal,
            ):
                raise PydanticCustomError(
                    _CHILD_DELIVERY_BINDING_MISMATCH,
                    _CHILD_DELIVERY_BINDING_MISMATCH_MESSAGE,
                )
        return self


def validate_ingest_delivery_receipt(
    envelope: IngestEnvelope,
    receipt: IngestDeliveryReceipt,
) -> None:
    if receipt.delivery_id != envelope.delivery_id:
        _fail("ingest_delivery_receipt_parent_delivery_mismatch")
    if receipt.envelope_sha256 != contract_sha256(envelope):
        _fail("ingest_delivery_receipt_envelope_sha256_mismatch")
    validated_receipt = IngestDeliveryReceipt.model_validate(receipt.model_dump())
    expected_units: tuple[tuple[IngestUnitKind, int | None], ...]
    attachment_units = tuple(
        (IngestUnitKind.ATTACHMENT, attachment.ordinal)
        for attachment in sorted(envelope.attachments, key=lambda attachment: attachment.ordinal)
    )
    match envelope.event_kind:
        case ConversationEventKind.ATTACHMENT_RECEIVED:
            expected_units = attachment_units
        case (
            ConversationEventKind.MESSAGE_FINALIZED
            | ConversationEventKind.MESSAGE_EDITED
            | ConversationEventKind.MESSAGE_DELETED
        ):
            expected_units = ((IngestUnitKind.MESSAGE, None), *attachment_units)
    actual_units = tuple((unit.kind, unit.ordinal) for unit in validated_receipt.unit_receipts)
    if any(unit not in actual_units for unit in expected_units):
        _fail("ingest_delivery_receipt_units_missing")
    if any(unit not in expected_units for unit in actual_units):
        _fail("ingest_delivery_receipt_units_extraneous")


def _fail(code: str) -> None:
    raise IngestDeliveryReceiptValidationError(code)


__all__ = [
    "IngestDeliveryReceipt",
    "IngestDeliveryReceiptValidationError",
    "IngestUnitKind",
    "IngestUnitReceipt",
    "ingest_unit_delivery_id",
    "validate_ingest_delivery_receipt",
]
