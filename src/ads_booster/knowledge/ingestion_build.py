from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import (
    ConversationEventKind,
    SourceDisposition,
    SourceKind,
)
from ads_booster.knowledge.extractors import EXTRACTOR_VERSION, ExtractionResult, extract
from ads_booster.knowledge.ingestion_files import SourceFileSet, prepare_source_files
from ads_booster.knowledge.operation_contracts import KnowledgeJob
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository_types import (
    IndexOutboxItem,
    JobRegistration,
    SourceObservationWrite,
    SourceRegistration,
)
from ads_booster.knowledge.source_contracts import IngestReceipt, QuoteRange, Source, SourceSegment
from ads_booster.knowledge.source_fetch import sanitize_persisted_url

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import ConversationEvent, IngestEnvelope
    from ads_booster.knowledge.source_fetch import FetchedSource

CURATION_POLICY_VERSION = "curation-policy.v1"
INDEXER_VERSION = "indexer.v1"


@dataclass(frozen=True, slots=True)
class SourceInput:
    source_identity: str
    source_kind: SourceKind
    revision: int
    original: bytes
    mime_type: str
    extraction_data: bytes
    extraction_mime_type: str
    sanitized_locator: str
    message: bytes | None
    fetched_source: FetchedSource | None = None


@dataclass(frozen=True, slots=True)
class IngestionBuildRequest:
    actor: ActorContext
    event: ConversationEvent
    envelope: IngestEnvelope
    source_input: SourceInput
    include_conversation_event: bool = True


def build_registration(
    repository: SqliteKnowledgeRepository,
    request: IngestionBuildRequest,
) -> SourceRegistration:
    extraction = extract(
        request.source_input.extraction_data,
        mime_type=request.source_input.extraction_mime_type,
    )
    source_id = stable_id(
        "source",
        request.event.scope.model_dump_json(),
        request.source_input.source_identity,
    )
    original_sha256 = sha256(request.source_input.original).hexdigest()
    revision_id = stable_id("revision", source_id, original_sha256)
    operation_id = stable_id("ingest", request.actor.workspace_id, request.envelope.delivery_id)
    receipt = _receipt(request.envelope.delivery_id, source_id, revision_id)
    source = _source(request, extraction, source_id, revision_id)
    prepared_files = prepare_source_files(
        repository.files,
        SourceFileSet(
            operation_id=operation_id,
            source_id=source_id,
            revision_id=revision_id,
            original=request.source_input.original,
            extraction=extraction,
            message=request.source_input.message,
        ),
    )
    return SourceRegistration(
        operation_id=operation_id,
        payload_sha256=_payload_sha256(request, original_sha256),
        source=source,
        segments=_segments(source, extraction),
        receipt=receipt,
        job=JobRegistration(
            job=_job(request.event, source, receipt, extraction),
            unique_key=(
                f"{source.workspace_id}:{source.revision_id}:"
                f"{EXTRACTOR_VERSION}:{CURATION_POLICY_VERSION}"
            ),
        ),
        index_item=IndexOutboxItem(
            item_id=receipt.index_operation_id,
            workspace_id=source.workspace_id,
            entity_kind="source",
            entity_id=source.source_id,
            revision_id=source.revision_id,
            extraction_version=EXTRACTOR_VERSION,
            admission_revision=0,
            indexer_version=INDEXER_VERSION,
        ),
        prepared_files=prepared_files,
        conversation_event=request.event if request.include_conversation_event else None,
        observation=_observation(request, source),
    )


def _source(
    request: IngestionBuildRequest,
    extraction: ExtractionResult,
    source_id: str,
    revision_id: str,
) -> Source:
    source_input = request.source_input
    event = request.event
    return Source(
        source_id=source_id,
        workspace_id=request.actor.workspace_id,
        scope=event.scope,
        owner_ref=request.actor.member_id,
        source_kind=source_input.source_kind,
        sanitized_locator=source_input.sanitized_locator,
        source_identity=source_input.source_identity,
        revision_id=revision_id,
        revision=source_input.revision,
        fetched_at=(
            source_input.fetched_source.fetched_at
            if source_input.fetched_source is not None
            else event.edited_at or event.created_at
        ),
        origin_created_at=event.created_at,
        sha256=sha256(source_input.original).hexdigest(),
        mime_type=source_input.mime_type,
        byte_length=len(source_input.original),
        extraction_status=extraction.extraction_status,
        completeness=extraction.completeness,
        extractor_version=extraction.extractor_version,
        disposition=SourceDisposition.PENDING,
        admission_revision=0,
        error_code=None if extraction.error_code is None else extraction.error_code.value,
    )


def _segments(source: Source, extraction: ExtractionResult) -> tuple[SourceSegment, ...]:
    return tuple(
        SourceSegment(
            source_id=source.source_id,
            revision_id=source.revision_id,
            segment_id=stable_id("segment", source.revision_id, str(index)),
            content_sha256=item.content_sha256,
            extraction_version=extraction.extractor_version,
            locator=item.locator,
            quote_range=QuoteRange(start=item.quote_start, end=item.quote_end),
            completeness=source.completeness,
        )
        for index, item in enumerate(extraction.segments, start=1)
    )


def _receipt(delivery_id: str, source_id: str, revision_id: str) -> IngestReceipt:
    return IngestReceipt(
        schema="knowledge.ingest-receipt.v1",
        delivery_id=delivery_id,
        source_id=source_id,
        source_revision_id=revision_id,
        curation_job_id=stable_id("job", revision_id, CURATION_POLICY_VERSION),
        index_operation_id=stable_id("index", revision_id, "0", INDEXER_VERSION),
        replayed=False,
    )


def _job(
    event: ConversationEvent,
    source: Source,
    receipt: IngestReceipt,
    extraction: ExtractionResult,
) -> KnowledgeJob:
    ready = bool(extraction.segments) or event.event_kind is ConversationEventKind.MESSAGE_DELETED
    urgent = event.event_kind in {
        ConversationEventKind.MESSAGE_EDITED,
        ConversationEventKind.MESSAGE_DELETED,
    }
    return KnowledgeJob(
        schema="knowledge.job.v1",
        job_id=receipt.curation_job_id,
        workspace_id=source.workspace_id,
        scope=source.scope,
        kind=JobKind.CURATION,
        state=JobState.QUEUED if ready else JobState.WAITING_DEPENDENCY,
        priority=JobPriority.URGENT if urgent else JobPriority.ROUTINE,
        root_event_id=stable_id(
            "event",
            event.conversation_id,
            event.message_id,
            str(event.revision),
        ),
        policy_version=CURATION_POLICY_VERSION,
        due_at=source.fetched_at,
        created_at=source.fetched_at,
        reason_code=None if ready else "extraction_unavailable",
    )


def _payload_sha256(request: IngestionBuildRequest, original_sha256: str) -> str:
    return sha256(
        contract_sha256(request.event).encode()
        + contract_sha256(request.envelope).encode()
        + original_sha256.encode()
    ).hexdigest()


def _observation(
    request: IngestionBuildRequest,
    source: Source,
) -> SourceObservationWrite | None:
    fetched = request.source_input.fetched_source
    if fetched is None:
        return None
    return SourceObservationWrite(
        observation_id=request.envelope.delivery_id,
        workspace_id=source.workspace_id,
        source_id=source.source_id,
        revision_id=source.revision_id,
        fetched_at=fetched.fetched_at,
        final_url=sanitize_persisted_url(fetched.final_url),
        http_status=fetched.status_code,
        etag=fetched.etag,
        last_modified=fetched.last_modified,
    )


def stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x00".join(parts).encode()).hexdigest()
    return f"{prefix}.{digest[:32]}"


def receipt_for_existing(
    delivery_id: str,
    source: Source,
    *,
    replayed: bool,
) -> IngestReceipt:
    return _receipt(delivery_id, source.source_id, source.revision_id).model_copy(
        update={"replayed": replayed}
    )


__all__ = [
    "IngestionBuildRequest",
    "SourceInput",
    "build_registration",
    "receipt_for_existing",
    "stable_id",
]
