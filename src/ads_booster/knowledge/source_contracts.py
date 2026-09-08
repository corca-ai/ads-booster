from __future__ import annotations

# ruff: noqa: EM101, TC001
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import (
    BoundedLocator,
    ConversationEventKind,
    ConversationRole,
    IngestEventKind,
    KnowledgeContractModel,
    MimeType,
    SourceCompleteness,
    SourceDisposition,
    SourceExtractionStatus,
    SourceKind,
    UtcDatetime,
)
from ads_booster.knowledge.scope_contracts import AccessScope


class Source(KnowledgeContractModel):
    source_id: BoundedId
    workspace_id: BoundedId
    scope: AccessScope
    owner_ref: BoundedId
    source_kind: SourceKind
    sanitized_locator: BoundedLocator
    source_identity: Annotated[str, Field(min_length=1, max_length=512)]
    revision_id: BoundedId
    revision: Annotated[int, Field(ge=1)]
    fetched_at: UtcDatetime
    origin_created_at: UtcDatetime | None = None
    sha256: Sha256Digest
    mime_type: MimeType
    byte_length: Annotated[int, Field(ge=0, le=52_428_800)]
    extraction_status: SourceExtractionStatus
    completeness: SourceCompleteness
    extractor_version: BoundedId
    disposition: SourceDisposition
    admission_revision: Annotated[int, Field(ge=0)]
    error_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None

    @model_validator(mode="after")
    def require_scope_workspace(self) -> Self:
        if self.scope.workspace_id != self.workspace_id:
            raise PydanticCustomError(
                "source_workspace_mismatch",
                "source workspace must match its access scope",
            )
        return self


class QuoteRange(KnowledgeContractModel):
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def require_ordered_range(self) -> Self:
        if self.end <= self.start:
            raise PydanticCustomError(
                "quote_range_not_ordered",
                "quote range end must follow its start",
            )
        return self


class SegmentLocator(KnowledgeContractModel):
    page: Annotated[int | None, Field(ge=1)] = None
    line_start: Annotated[int | None, Field(ge=1)] = None
    line_end: Annotated[int | None, Field(ge=1)] = None
    paragraph: Annotated[int | None, Field(ge=1)] = None
    time_start_ms: Annotated[int | None, Field(ge=0)] = None
    time_end_ms: Annotated[int | None, Field(ge=0)] = None

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> Self:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise PydanticCustomError(
                "segment_line_range_not_ordered",
                "segment line end cannot precede its start",
            )
        if (
            self.time_start_ms is not None
            and self.time_end_ms is not None
            and self.time_end_ms < self.time_start_ms
        ):
            raise PydanticCustomError(
                "segment_time_range_not_ordered",
                "segment time end cannot precede its start",
            )
        return self


class SourceSegment(KnowledgeContractModel):
    source_id: BoundedId
    revision_id: BoundedId
    segment_id: BoundedId
    content_sha256: Sha256Digest
    extraction_version: BoundedId
    locator: SegmentLocator
    quote_range: QuoteRange
    completeness: SourceCompleteness


class QuotedSpan(KnowledgeContractModel):
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    source_message_id: BoundedId

    @model_validator(mode="after")
    def require_ordered_span(self) -> Self:
        if self.end <= self.start:
            raise PydanticCustomError(
                "quoted_span_not_ordered",
                "quoted span end must follow its start",
            )
        return self


class ConversationEvent(KnowledgeContractModel):
    schema_version: Literal["knowledge.conversation-event.v1"] = "knowledge.conversation-event.v1"
    conversation_id: BoundedId
    message_id: BoundedId
    revision: Annotated[int, Field(ge=1)]
    sequence: Annotated[int, Field(ge=1)]
    role: ConversationRole
    speaker_ref: BoundedId
    created_at: UtcDatetime
    edited_at: UtcDatetime | None = None
    reply_to: BoundedId | None = None
    text: Annotated[str, Field(max_length=50_000)]
    quoted_spans: Annotated[tuple[QuotedSpan, ...], Field(max_length=128)] = ()
    event_kind: ConversationEventKind
    scope: AccessScope

    @model_validator(mode="after")
    def require_event_shape(self) -> Self:
        match self.event_kind:  # noqa: MATCH_OK
            case ConversationEventKind.MESSAGE_FINALIZED:
                if not self.text:
                    raise PydanticCustomError(
                        "finalized_message_requires_text",
                        "finalized messages require text",
                    )
            case ConversationEventKind.MESSAGE_EDITED:
                if self.edited_at is None or not self.text:
                    raise PydanticCustomError(
                        "edited_message_requires_text_and_time",
                        "edited messages require text and edited time",
                    )
            case ConversationEventKind.MESSAGE_DELETED:
                if self.text:
                    raise PydanticCustomError(
                        "deleted_message_forbids_text",
                        "deleted messages cannot retain text",
                    )
            case ConversationEventKind.ATTACHMENT_RECEIVED:
                pass
        return self


class MessageEventRef(KnowledgeContractModel):
    conversation_ref: BoundedId
    message_ref: BoundedId
    revision: Annotated[int, Field(ge=1)]
    logical_source_ref: BoundedId | None = None
    logical_revision_ref: BoundedId | None = None
    corrects_revision_ref: BoundedId | None = None

    @model_validator(mode="after")
    def require_complete_source_reference(self) -> Self:
        if (self.logical_source_ref is None) != (self.logical_revision_ref is None):
            raise PydanticCustomError(
                "partial_logical_source_reference",
                "logical source and revision references must appear together",
            )
        return self


class ConversationRef(KnowledgeContractModel):
    conversation_ref: BoundedId
    message_ref: BoundedId
    revision: Annotated[int, Field(ge=1)]


class AttachmentCapability(KnowledgeContractModel):
    ordinal: Annotated[int, Field(ge=0, le=255)]
    logical_source_ref: BoundedId
    logical_revision_ref: BoundedId
    mime_type: MimeType
    capability_ref: BoundedId | None = None
    source_url: Annotated[str, Field(min_length=1, max_length=4_096)] | None = None


class TrustedCapabilityRefs(KnowledgeContractModel):
    brand_ref: BoundedId
    grant: Literal["brand_voice_edit"]


class LogicalPageRefs(KnowledgeContractModel):
    source: BoundedId
    target: BoundedId | None = None
    new_page: BoundedId | None = None

    @model_validator(mode="after")
    def require_one_page_destination(self) -> Self:
        if (self.target is None) == (self.new_page is None):
            raise PydanticCustomError(
                "logical_page_destination_invalid",
                "logical page references require exactly one destination",
            )
        return self


class IngestEnvelope(KnowledgeContractModel):
    schema_version: Literal["knowledge.ingest-envelope.v1"] = Field(alias="schema")
    delivery_id: BoundedId
    event_kind: IngestEventKind
    request_text: Annotated[str, Field(max_length=50_000)]
    message_event: MessageEventRef | None = None
    conversation: ConversationRef | None = None
    attachments: Annotated[tuple[AttachmentCapability, ...], Field(max_length=256)] = ()
    timestamp: UtcDatetime
    brand_ref: BoundedId | None = None
    trusted_capability_refs: TrustedCapabilityRefs | None = None
    logical_page_refs: LogicalPageRefs | None = None

    @model_validator(mode="after")
    def require_ingest_shape(self) -> Self:
        ordinals = tuple(item.ordinal for item in self.attachments)
        if len(ordinals) != len(set(ordinals)):
            raise PydanticCustomError(
                "attachment_ordinals_not_unique",
                "attachment ordinals must be unique",
            )
        match self.event_kind:  # noqa: MATCH_OK
            case IngestEventKind.ATTACHMENT_RECEIVED:
                if not self.attachments:
                    raise PydanticCustomError(
                        "attachment_event_requires_attachment",
                        "attachment events require at least one attachment",
                    )
            case IngestEventKind.MESSAGE_FINALIZED | IngestEventKind.MESSAGE_EDITED:
                if self.message_event is None or not self.request_text:
                    raise PydanticCustomError(
                        "message_event_requires_reference_and_text",
                        "message events require a message reference and text",
                    )
            case IngestEventKind.MESSAGE_DELETED:
                if self.message_event is None or self.request_text:
                    raise PydanticCustomError(
                        "deleted_event_requires_reference_without_text",
                        "deleted events require a message reference and no text",
                    )
        return self


class IngestReceipt(KnowledgeContractModel):
    schema_version: Literal["knowledge.ingest-receipt.v1"] = Field(alias="schema")
    delivery_id: BoundedId
    source_id: BoundedId
    source_revision_id: BoundedId
    curation_job_id: BoundedId
    index_operation_id: BoundedId
    replayed: bool
