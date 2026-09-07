from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Never, Protocol, final, override

from ads_booster.knowledge.contract_types import SourceKind
from ads_booster.knowledge.ingestion_build import SourceInput, receipt_for_existing
from ads_booster.knowledge.repository_types import SourceObservationWrite
from ads_booster.knowledge.source_fetch import SourceFetchRequest, sanitize_persisted_url

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import (
        AttachmentCapability,
        IngestEnvelope,
        IngestReceipt,
        Source,
    )
    from ads_booster.knowledge.source_fetch import FetchedSource, SourceFetcher


@dataclass(slots=True)
class IngestionError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class TrustedSourcePayload:
    logical_source_ref: str
    logical_revision_ref: str
    data: bytes
    mime_type: str
    sanitized_locator: str


class AttachmentReader(Protocol):
    def read(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> TrustedSourcePayload: ...


@dataclass(frozen=True, slots=True)
class _ExistingSource:
    source: Source
    fetched: FetchedSource | None


@final
class AttachmentSourcePreparer:
    def __init__(
        self,
        repository: SqliteKnowledgeRepository,
        reader: AttachmentReader | None,
        fetcher: SourceFetcher | None,
    ) -> None:
        """Accept only scoped byte and URL capabilities supplied by trusted ingress."""
        self._repository = repository
        self._reader = reader
        self._fetcher = fetcher

    def prepare(
        self,
        actor: ActorContext,
        envelope: IngestEnvelope,
    ) -> SourceInput | IngestReceipt:
        if len(envelope.attachments) != 1:
            _fail("single_attachment_unit_required")
        attachment = envelope.attachments[0]
        if attachment.source_url is not None:
            prepared = self._url_input(actor, attachment)
        else:
            prepared = self._file_input(actor, attachment)
        if isinstance(prepared, _ExistingSource):
            return self._observe_existing(actor, envelope, prepared)
        return prepared

    def _url_input(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> SourceInput | _ExistingSource:
        if self._fetcher is None or attachment.source_url is None:
            _fail("url_fetcher_unavailable")
        identity = f"url:{attachment.logical_source_ref}"
        current = self._repository.source_ingest_head(actor, SourceKind.URL, identity)
        previous = (
            None
            if current is None
            else self._repository.latest_source_observation(actor, current.source_id)
        )
        fetched = self._fetcher.fetch(
            SourceFetchRequest(
                url=attachment.source_url,
                etag=None if previous is None else previous.etag,
                last_modified=None if previous is None else previous.last_modified,
            )
        )
        if fetched.not_modified:
            if current is None:
                _fail("not_modified_without_source")
            return _ExistingSource(source=current, fetched=fetched)
        if current is not None and current.sha256 == sha256(fetched.body).hexdigest():
            return _ExistingSource(source=current, fetched=fetched)
        return SourceInput(
            source_identity=identity,
            source_kind=SourceKind.URL,
            revision=1 if current is None else current.revision + 1,
            original=fetched.body,
            mime_type=fetched.mime_type,
            extraction_data=fetched.body,
            extraction_mime_type=fetched.mime_type,
            sanitized_locator=sanitize_persisted_url(fetched.final_url),
            message=None,
            fetched_source=fetched,
        )

    def _file_input(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> SourceInput | _ExistingSource:
        if self._reader is None:
            _fail("attachment_reader_unavailable")
        payload = self._reader.read(actor, attachment)
        if (
            payload.logical_source_ref != attachment.logical_source_ref
            or payload.logical_revision_ref != attachment.logical_revision_ref
            or payload.mime_type != attachment.mime_type
        ):
            _fail("attachment_capability_binding_mismatch")
        identity = f"attachment:{payload.logical_source_ref}"
        current = self._repository.source_ingest_head(actor, SourceKind.FILE, identity)
        if current is not None and current.sha256 == sha256(payload.data).hexdigest():
            return _ExistingSource(source=current, fetched=None)
        return SourceInput(
            source_identity=identity,
            source_kind=SourceKind.FILE,
            revision=1 if current is None else current.revision + 1,
            original=payload.data,
            mime_type=payload.mime_type,
            extraction_data=payload.data,
            extraction_mime_type=payload.mime_type,
            sanitized_locator=payload.sanitized_locator,
            message=None,
        )

    def _observe_existing(
        self,
        actor: ActorContext,
        envelope: IngestEnvelope,
        existing: _ExistingSource,
    ) -> IngestReceipt:
        fetched = existing.fetched
        if fetched is None:
            return receipt_for_existing(envelope.delivery_id, existing.source, replayed=True)
        inserted = self._repository.record_source_observation(
            actor,
            SourceObservationWrite(
                observation_id=envelope.delivery_id,
                workspace_id=actor.workspace_id,
                source_id=existing.source.source_id,
                revision_id=existing.source.revision_id,
                fetched_at=fetched.fetched_at,
                final_url=sanitize_persisted_url(fetched.final_url),
                http_status=fetched.status_code,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
            ),
        )
        return receipt_for_existing(
            envelope.delivery_id,
            existing.source,
            replayed=not inserted,
        )


def _fail(code: str) -> Never:
    raise IngestionError(code)


__all__ = [
    "AttachmentReader",
    "AttachmentSourcePreparer",
    "IngestionError",
    "TrustedSourcePayload",
]
