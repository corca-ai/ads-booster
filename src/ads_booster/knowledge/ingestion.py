from __future__ import annotations

from typing import TYPE_CHECKING, final

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import ConversationEventKind, SourceKind
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.ingest_receipts import (
    IngestDeliveryReceipt,
    IngestUnitKind,
    IngestUnitReceipt,
    ingest_unit_delivery_id,
)
from ads_booster.knowledge.ingestion_build import (
    IngestionBuildRequest,
    SourceInput,
    build_registration,
)
from ads_booster.knowledge.ingestion_sources import (
    AttachmentReader,
    AttachmentSourcePreparer,
    IngestionError,
    TrustedSourcePayload,
)
from ads_booster.knowledge.messages import prepare_message, require_actor_event_binding

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import (
        ConversationEvent,
        IngestEnvelope,
        IngestReceipt,
    )
    from ads_booster.knowledge.source_fetch import SourceFetcher


@final
class KnowledgeIngestion:
    def __init__(
        self,
        repository: SqliteKnowledgeRepository,
        *,
        attachment_reader: AttachmentReader | None = None,
        url_fetcher: SourceFetcher | None = None,
    ) -> None:
        """Bind trusted ingress capabilities to the canonical knowledge catalog."""
        self._repository = repository
        self._attachments = AttachmentSourcePreparer(
            repository,
            attachment_reader,
            url_fetcher,
        )

    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt:
        _ = authorize_write(actor=actor, target_scope=event.scope, at=envelope.timestamp)
        require_actor_event_binding(actor, event)
        units: list[IngestUnitReceipt] = []
        include_event = True
        if event.event_kind is not ConversationEventKind.ATTACHMENT_RECEIVED:
            units.append(self._message_unit(actor, event, envelope))
            include_event = False
        for attachment in sorted(envelope.attachments, key=lambda item: item.ordinal):
            unit_envelope = envelope.model_copy(
                update={
                    "delivery_id": ingest_unit_delivery_id(
                        envelope.delivery_id,
                        IngestUnitKind.ATTACHMENT,
                        attachment.ordinal,
                    ),
                    "attachments": (attachment,),
                }
            )
            units.append(
                IngestUnitReceipt(
                    kind=IngestUnitKind.ATTACHMENT,
                    ordinal=attachment.ordinal,
                    receipt=self._ingest_attachment_unit(
                        actor,
                        event,
                        unit_envelope,
                        include_event=include_event,
                    ),
                )
            )
            include_event = False
        return IngestDeliveryReceipt(
            delivery_id=envelope.delivery_id,
            envelope_sha256=contract_sha256(envelope),
            unit_receipts=tuple(units),
        )

    def _message_unit(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestUnitReceipt:
        unit_envelope = envelope.model_copy(
            update={
                "delivery_id": ingest_unit_delivery_id(
                    envelope.delivery_id,
                    IngestUnitKind.MESSAGE,
                    None,
                ),
                "attachments": (),
            }
        )
        prepared = prepare_message(actor, event, unit_envelope)
        source_input = SourceInput(
            source_identity=prepared.source_identity,
            source_kind=SourceKind.MESSAGE,
            revision=event.revision,
            original=prepared.message_bytes,
            mime_type="application/json",
            extraction_data=prepared.extracted_bytes,
            extraction_mime_type="text/plain",
            sanitized_locator=prepared.sanitized_locator,
            message=prepared.message_bytes,
        )
        return IngestUnitReceipt(
            kind=IngestUnitKind.MESSAGE,
            receipt=self._register_unit(
                actor,
                event,
                unit_envelope,
                source_input,
                include_event=True,
            ),
        )

    def _ingest_attachment_unit(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
        *,
        include_event: bool,
    ) -> IngestReceipt:
        prepared_attachment = self._attachments.prepare(actor, envelope)
        if not isinstance(prepared_attachment, SourceInput):
            return prepared_attachment
        return self._register_unit(
            actor,
            event,
            envelope,
            prepared_attachment,
            include_event=include_event,
        )

    def _register_unit(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
        source_input: SourceInput,
        *,
        include_event: bool,
    ) -> IngestReceipt:
        command = build_registration(
            self._repository,
            IngestionBuildRequest(
                actor=actor,
                event=event,
                envelope=envelope,
                source_input=source_input,
                include_conversation_event=include_event,
            ),
        )
        return self._repository.register_source(command)


__all__ = [
    "AttachmentReader",
    "IngestionError",
    "KnowledgeIngestion",
    "TrustedSourcePayload",
]
