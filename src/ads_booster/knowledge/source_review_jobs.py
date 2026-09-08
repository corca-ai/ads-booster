from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, Final, assert_never

from ads_booster.knowledge.contract_types import (
    ConversationEventKind,
    ConversationRole,
    IngestEventKind,
    SourceKind,
)
from ads_booster.knowledge.ingestion_build import (
    IngestionBuildRequest,
    SourceInput,
    build_registration,
    stable_id,
)
from ads_booster.knowledge.jobs import JobProcessResult
from ads_booster.knowledge.operation_enums import JobKind, JobState
from ads_booster.knowledge.repository_types import JobLease, SourceObservationWrite
from ads_booster.knowledge.source_contracts import (
    AttachmentCapability,
    ConversationEvent,
    IngestEnvelope,
)
from ads_booster.knowledge.source_fetch import (
    FetchedSource,
    SourceFetcher,
    SourceFetchError,
    SourceFetchRequest,
    sanitize_persisted_url,
)

if TYPE_CHECKING:
    from threading import Event

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import Source

_RETRY_DELAYS: Final = (5.0, 30.0, 120.0)


@unique
class SourceReviewStatus(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    NOT_FOUND = "not_found"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceReviewJobProcessor:
    repository: SqliteKnowledgeRepository
    actor: ActorContext
    fetcher: SourceFetcher

    def process(  # noqa: PLR0911
        self,
        lease: JobLease,
        cancellation: Event,
    ) -> JobProcessResult:
        if lease.job.kind is not JobKind.SOURCE_REVIEW:
            return _result(lease, JobState.FAILED, SourceReviewStatus.FAILED, "job_kind_invalid")
        scheduled = self.repository.scheduled_job_request(self.actor, lease.job.job_id)
        if scheduled is None or len(scheduled.targets) != 1:
            return _result(
                lease,
                JobState.FAILED,
                SourceReviewStatus.FAILED,
                "source_review_target_invalid",
            )
        stored = self.repository.read_source(self.actor, scheduled.targets[0])
        if stored is None or stored.source.source_kind is not SourceKind.URL:
            return _result(
                lease,
                JobState.FAILED,
                SourceReviewStatus.FAILED,
                "source_review_source_unavailable",
            )
        previous = self.repository.latest_source_observation(
            self.actor,
            stored.source.source_id,
        )
        url = stored.source.sanitized_locator if previous is None else previous.final_url
        request = SourceFetchRequest(
            url=url,
            etag=None if previous is None else previous.etag,
            last_modified=None if previous is None else previous.last_modified,
        )
        fetched = self._fetch(lease, stored.source, request, cancellation)
        match fetched:
            case JobProcessResult():
                return fetched
            case FetchedSource():
                pass
            case unreachable:
                assert_never(unreachable)
        if cancellation.is_set():
            return _result(
                lease,
                JobState.CANCELLED,
                SourceReviewStatus.CANCELLED,
                "source_review_cancelled",
            )
        if fetched.not_modified or sha256(fetched.body).hexdigest() == stored.source.sha256:
            self._record_observation(lease, stored.source, fetched)
            return _result(
                lease,
                JobState.COMPLETED,
                SourceReviewStatus.UNCHANGED,
                None,
                revision_id=stored.source.revision_id,
            )
        registration = build_registration(
            self.repository,
            IngestionBuildRequest(
                actor=self.actor,
                event=_review_event(lease, stored.source, fetched),
                envelope=_review_envelope(lease, stored.source, fetched, scheduled.purpose),
                source_input=SourceInput(
                    source_identity=stored.source.source_identity,
                    source_kind=SourceKind.URL,
                    revision=stored.source.revision + 1,
                    original=fetched.body,
                    mime_type=fetched.mime_type,
                    extraction_data=fetched.body,
                    extraction_mime_type=fetched.mime_type,
                    sanitized_locator=sanitize_persisted_url(fetched.final_url),
                    message=None,
                    fetched_source=fetched,
                ),
                include_conversation_event=False,
            ),
        )
        receipt = self.repository.register_source(registration)
        return _result(
            lease,
            JobState.COMPLETED,
            SourceReviewStatus.UPDATED,
            None,
            revision_id=receipt.source_revision_id,
        )

    def _fetch(
        self,
        lease: JobLease,
        source: Source,
        request: SourceFetchRequest,
        cancellation: Event,
    ) -> FetchedSource | JobProcessResult:
        for delay in (*_RETRY_DELAYS, None):
            if cancellation.is_set():
                return _result(
                    lease,
                    JobState.CANCELLED,
                    SourceReviewStatus.CANCELLED,
                    "source_review_cancelled",
                )
            try:
                return self.fetcher.fetch(request)
            except SourceFetchError as error:
                if error.code == "http_not_found":
                    self._record_not_found(lease, source, request)
                    return _result(
                        lease,
                        JobState.COMPLETED,
                        SourceReviewStatus.NOT_FOUND,
                        "source_review_origin_not_found",
                        revision_id=source.revision_id,
                    )
                if not error.retryable or delay is None:
                    return _result(
                        lease,
                        JobState.FAILED,
                        SourceReviewStatus.FAILED,
                        error.code,
                        revision_id=source.revision_id,
                    )
                if cancellation.wait(delay):
                    return _result(
                        lease,
                        JobState.CANCELLED,
                        SourceReviewStatus.CANCELLED,
                        "source_review_cancelled",
                    )
        return _result(
            lease,
            JobState.FAILED,
            SourceReviewStatus.FAILED,
            "source_review_retry_state_invalid",
        )

    def _record_observation(
        self,
        lease: JobLease,
        source: Source,
        fetched: FetchedSource,
    ) -> None:
        _ = self.repository.record_source_observation(
            self.actor,
            SourceObservationWrite(
                observation_id=stable_id(
                    "source-review-observation",
                    lease.job.job_id,
                    str(lease.lease_generation),
                    source.revision_id,
                ),
                workspace_id=self.actor.workspace_id,
                source_id=source.source_id,
                revision_id=source.revision_id,
                fetched_at=fetched.fetched_at,
                final_url=sanitize_persisted_url(fetched.final_url),
                http_status=fetched.status_code,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
            ),
        )

    def _record_not_found(
        self,
        lease: JobLease,
        source: Source,
        request: SourceFetchRequest,
    ) -> None:
        _ = self.repository.record_source_observation(
            self.actor,
            SourceObservationWrite(
                observation_id=stable_id(
                    "source-review-observation",
                    lease.job.job_id,
                    str(lease.lease_generation),
                    source.revision_id,
                ),
                workspace_id=self.actor.workspace_id,
                source_id=source.source_id,
                revision_id=source.revision_id,
                fetched_at=datetime.now(UTC),
                final_url=sanitize_persisted_url(request.url),
                http_status=404,
            ),
        )


def _review_event(
    lease: JobLease,
    source: Source,
    fetched: FetchedSource,
) -> ConversationEvent:
    return ConversationEvent(
        conversation_id=stable_id("source-review", source.source_id),
        message_id=stable_id(
            "source-review-message",
            lease.job.job_id,
            fetched.fetched_at.isoformat(),
        ),
        revision=source.revision + 1,
        sequence=source.revision + 1,
        role=ConversationRole.TOOL,
        speaker_ref="knowledge.source-review",
        created_at=fetched.fetched_at,
        text="",
        event_kind=ConversationEventKind.ATTACHMENT_RECEIVED,
        scope=source.scope,
    )


def _review_envelope(
    lease: JobLease,
    source: Source,
    fetched: FetchedSource,
    purpose: str,
) -> IngestEnvelope:
    return IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=stable_id(
            "source-review-delivery",
            lease.job.job_id,
            str(lease.lease_generation),
            sha256(fetched.body).hexdigest(),
        ),
        event_kind=IngestEventKind.ATTACHMENT_RECEIVED,
        request_text=purpose,
        attachments=(
            AttachmentCapability(
                ordinal=0,
                logical_source_ref=source.source_id,
                logical_revision_ref=source.revision_id,
                mime_type=fetched.mime_type,
                source_url=fetched.final_url,
            ),
        ),
        timestamp=fetched.fetched_at,
    )


def _result(
    lease: JobLease,
    state: JobState,
    status: SourceReviewStatus,
    error_code: str | None,
    *,
    revision_id: str | None = None,
) -> JobProcessResult:
    return JobProcessResult(
        state=state,
        payload=json.dumps(
            {
                "schema": "knowledge.source-review-result.v1",
                "job_id": lease.job.job_id,
                "source_revision_id": revision_id,
                "status": status.value,
                "error_code": error_code,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    )


__all__ = ["SourceReviewJobProcessor", "SourceReviewStatus"]
