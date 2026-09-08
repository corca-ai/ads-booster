from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AttachmentCapability,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    GrantCapability,
    IngestEnvelope,
    IngestReceipt,
    MessageEventRef,
    ScopeGrant,
    ScopeKind,
    SourceExtractionStatus,
)
from ads_booster.knowledge.file_store import SourceFileKind, SourceRevisionTarget
from ads_booster.knowledge.ingest_receipts import IngestDeliveryReceipt, IngestUnitKind
from ads_booster.knowledge.ingestion import KnowledgeIngestion, TrustedSourcePayload
from ads_booster.knowledge.repository import (
    MembershipRole,
    RepositoryConflictError,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.source_fetch import FetchedSource, SourceFetchRequest
from tests.knowledge.ingestion_test_fixtures import pdf_bytes

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
_VISIBILITY_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_COUNT_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])
_OBSERVATION_ROWS: TypeAdapter[list[tuple[str, int]]] = TypeAdapter(list[tuple[str, int]])


def _only_unit(receipt: IngestDeliveryReceipt) -> IngestReceipt:
    assert len(receipt.unit_receipts) == 1
    return receipt.unit_receipts[0].receipt


def _scope(workspace_id: str = "workspace.a") -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_id)


def _actor(workspace_id: str = "workspace.a") -> ActorContext:
    scope = _scope(workspace_id)
    grants = tuple(
        ScopeGrant(
            grant_id=f"grant.{capability.value}.{workspace_id}",
            capability=capability,
            workspace_id=workspace_id,
            scope=scope,
            policy_epoch=1,
            effective_at=NOW,
        )
        for capability in (GrantCapability.READ, GrantCapability.WRITE)
    )
    return ActorContext(
        actor_id=f"member.{workspace_id}",
        workspace_id=workspace_id,
        member_id=f"member.{workspace_id}",
        session_id=f"session.{workspace_id}",
        conversation_scope=scope,
        grants=grants,
        policy_epoch=1,
        authenticated_at=NOW,
    )


def _event(
    *,
    revision: int = 1,
    kind: ConversationEventKind = ConversationEventKind.MESSAGE_FINALIZED,
    text: str = "가격은 31,000원입니다.",
    workspace_id: str = "workspace.a",
) -> ConversationEvent:
    return ConversationEvent(
        conversation_id="conversation.a",
        message_id="message.a",
        revision=revision,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=f"member.{workspace_id}",
        created_at=NOW,
        edited_at=(
            NOW + timedelta(minutes=revision)
            if kind is ConversationEventKind.MESSAGE_EDITED
            else None
        ),
        text=text,
        event_kind=kind,
        scope=_scope(workspace_id),
    )


def _envelope(event: ConversationEvent, delivery_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=delivery_id,
        event_kind=event.event_kind,
        request_text=event.text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id,
            message_ref=event.message_id,
            revision=event.revision,
        ),
        timestamp=event.edited_at or event.created_at,
    )


def test_plain_message_ingest_publishes_exact_evidence_and_atomic_pending_work(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    event = _event()
    envelope = _envelope(event, "delivery.message.1")

    # When
    receipt = _only_unit(KnowledgeIngestion(repository).ingest(actor, event, envelope))

    # Then
    stored = repository.read_source(actor, receipt.source_id)
    assert stored is not None
    assert stored.source.disposition.value == "pending"
    assert stored.source.admission_revision == 0
    assert stored.body == event.model_dump_json(by_alias=True).encode()
    assert stored.source.sha256 == sha256(stored.body).hexdigest()
    assert stored.segments[0].content_sha256 == sha256(event.text.encode()).hexdigest()
    assert repository.pending_index_items(actor.workspace_id) == (receipt.index_operation_id,)
    extracted = repository.files.published(
        SourceRevisionTarget(
            source_id=receipt.source_id,
            revision_id=receipt.source_revision_id,
            file_kind=SourceFileKind.EXTRACTED,
        ),
        sha256(event.text.encode()).hexdigest(),
    )
    assert repository.files.read(extracted) == event.text.encode()


def test_duplicate_delivery_replays_ids_and_changed_payload_conflicts(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    event = _event()
    envelope = _envelope(event, "delivery.replay")
    ingestion = KnowledgeIngestion(repository)
    first = _only_unit(ingestion.ingest(actor, event, envelope))

    # When
    replay = _only_unit(ingestion.ingest(actor, event, envelope))

    # Then
    assert replay.model_copy(update={"replayed": False}) == first
    assert replay.replayed is True
    changed = _event(text="가격은 32,000원입니다.")
    with pytest.raises(RepositoryConflictError, match="operation_idempotency_conflict"):
        _ = ingestion.ingest(actor, changed, _envelope(changed, envelope.delivery_id))


def test_source_identity_isolated_by_workspace_before_catalog_lookup(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor_a = _actor("workspace.a")
    actor_b = _actor("workspace.b")
    repository.register_actor(actor_a, MembershipRole.EDITOR)
    repository.register_actor(actor_b, MembershipRole.EDITOR)
    event_a = _event(workspace_id="workspace.a")
    event_b = _event(workspace_id="workspace.b")

    # When
    receipt_a = _only_unit(
        KnowledgeIngestion(repository).ingest(
            actor_a, event_a, _envelope(event_a, "delivery.scope.a")
        )
    )
    receipt_b = _only_unit(
        KnowledgeIngestion(repository).ingest(
            actor_b, event_b, _envelope(event_b, "delivery.scope.b")
        )
    )

    # Then
    assert receipt_a.source_id != receipt_b.source_id
    assert repository.read_source(actor_b, receipt_a.source_id) is None


def test_edit_and_delete_advance_head_while_late_revision_cannot_overwrite(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    ingestion = KnowledgeIngestion(repository)
    first = _event()
    _ = ingestion.ingest(actor, first, _envelope(first, "delivery.progress.1"))
    edited = _event(
        revision=2,
        kind=ConversationEventKind.MESSAGE_EDITED,
        text="가격은 32,000원입니다.",
    )
    edit_receipt = _only_unit(
        ingestion.ingest(actor, edited, _envelope(edited, "delivery.progress.2"))
    )

    # When
    with pytest.raises(RepositoryConflictError, match="source_revision_sequence_conflict"):
        _ = ingestion.ingest(actor, first, _envelope(first, "delivery.progress.late"))
    deleted = _event(revision=3, kind=ConversationEventKind.MESSAGE_DELETED, text="")
    delete_receipt = _only_unit(
        ingestion.ingest(actor, deleted, _envelope(deleted, "delivery.progress.3"))
    )

    # Then
    assert edit_receipt.source_id == delete_receipt.source_id
    stored = repository.read_source(actor, delete_receipt.source_id)
    assert stored is None
    with closing(sqlite3.connect(repository.database_path)) as database:
        visibility = _VISIBILITY_ROW.validate_python(
            database.execute(
                "SELECT visibility FROM sources WHERE workspace_id=? AND source_id=?",
                (actor.workspace_id, delete_receipt.source_id),
            ).fetchone()
        )
    assert visibility == ("blocked",)


@dataclass(frozen=True, slots=True)
class FixtureAttachmentReader:
    payload: TrustedSourcePayload

    def read(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> TrustedSourcePayload:
        assert actor.workspace_id == "workspace.a"
        assert attachment.logical_source_ref == self.payload.logical_source_ref
        return self.payload


@pytest.mark.parametrize(
    ("mime_type", "payload", "expected_status", "expected_text"),
    [
        ("text/plain", b"plain source", SourceExtractionStatus.COMPLETE, "plain source"),
        (
            "text/html",
            b"<html><script>secret()</script><p>visible source</p></html>",
            SourceExtractionStatus.COMPLETE,
            "visible source",
        ),
        (
            "application/octet-stream",
            bytes((0, 1, 2)),
            SourceExtractionStatus.NEEDS_EXTRACTOR,
            "",
        ),
        (
            "application/pdf",
            pdf_bytes("pdf source"),
            SourceExtractionStatus.COMPLETE,
            "pdf source",
        ),
    ],
)
def test_attachment_ingest_retains_original_and_reports_honest_extraction(
    tmp_path: Path,
    mime_type: str,
    payload: bytes,
    expected_status: SourceExtractionStatus,
    expected_text: str,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    event = _event(kind=ConversationEventKind.ATTACHMENT_RECEIVED, text="")
    attachment = AttachmentCapability(
        ordinal=0,
        logical_source_ref="upload.a",
        logical_revision_ref="upload.a.rev1",
        mime_type=mime_type,
        capability_ref="capability.a",
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=f"delivery.attachment.{expected_status.value}",
        event_kind=event.event_kind,
        request_text="",
        attachments=(attachment,),
        timestamp=NOW,
    )
    reader = FixtureAttachmentReader(
        TrustedSourcePayload(
            logical_source_ref=attachment.logical_source_ref,
            logical_revision_ref=attachment.logical_revision_ref,
            data=payload,
            mime_type=mime_type,
            sanitized_locator="upload.bin",
        )
    )

    # When
    receipt = _only_unit(
        KnowledgeIngestion(repository, attachment_reader=reader).ingest(actor, event, envelope)
    )

    # Then
    stored = repository.read_source(actor, receipt.source_id)
    assert stored is not None
    assert stored.body == payload
    assert stored.source.extraction_status is expected_status
    extracted_digest = sha256(expected_text.encode()).hexdigest()
    extracted = repository.files.published(
        SourceRevisionTarget(
            source_id=receipt.source_id,
            revision_id=receipt.source_revision_id,
            file_kind=SourceFileKind.EXTRACTED,
        ),
        extracted_digest,
    )
    assert repository.files.read(extracted).decode() == expected_text


@dataclass(slots=True)
class SequencedFetcher:
    responses: list[FetchedSource]
    requests: list[SourceFetchRequest]

    def fetch(self, request: SourceFetchRequest) -> FetchedSource:
        self.requests.append(request)
        return self.responses.pop(0)


def test_url_304_records_observation_without_creating_revision(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    event = _event(kind=ConversationEventKind.ATTACHMENT_RECEIVED, text="")
    attachment = AttachmentCapability(
        ordinal=0,
        logical_source_ref="url.source",
        logical_revision_ref="url.source.rev1",
        mime_type="text/plain",
        source_url="https://public.example/source",
    )
    first_envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.url.first",
        event_kind=event.event_kind,
        request_text="",
        attachments=(attachment,),
        timestamp=NOW,
    )
    fetcher = SequencedFetcher(
        responses=[
            FetchedSource(
                original_url=attachment.source_url or "",
                final_url=(
                    "https://user:pass@[2001:db8::1]:8443/source?private=removed#token=secret"
                ),
                status_code=200,
                body=b"version one",
                mime_type="text/plain",
                etag='"v1"',
                last_modified="Sun, 07 Sep 2026 12:00:00 GMT",
                fetched_at=NOW,
                not_modified=False,
            ),
            FetchedSource(
                original_url=attachment.source_url or "",
                final_url=(
                    "https://user:pass@[2001:db8::1]:8443/source?private=removed#token=secret"
                ),
                status_code=304,
                body=b"",
                mime_type="application/octet-stream",
                etag='"v1"',
                last_modified="Sun, 07 Sep 2026 12:00:00 GMT",
                fetched_at=NOW + timedelta(minutes=1),
                not_modified=True,
            ),
        ],
        requests=[],
    )
    ingestion = KnowledgeIngestion(repository, url_fetcher=fetcher)
    first = _only_unit(ingestion.ingest(actor, event, first_envelope))

    # When
    second = _only_unit(
        ingestion.ingest(
            actor,
            event,
            first_envelope.model_copy(update={"delivery_id": "delivery.url.second"}),
        )
    )

    # Then
    assert second.source_revision_id == first.source_revision_id
    assert fetcher.requests[1].etag == '"v1"'
    stored = repository.read_source(actor, first.source_id)
    assert stored is not None
    assert stored.source.sanitized_locator == "https://[2001:db8::1]:8443/source"
    with closing(sqlite3.connect(repository.database_path)) as database:
        revisions = _COUNT_ROW.validate_python(
            database.execute("SELECT COUNT(*) FROM source_revisions").fetchone()
        )
        observations = _OBSERVATION_ROWS.validate_python(
            database.execute(
                "SELECT final_url,http_status FROM source_observations ORDER BY observed_at"
            ).fetchall()
        )
    assert revisions == (1,)
    assert observations == [
        ("https://[2001:db8::1]:8443/source", 200),
        ("https://[2001:db8::1]:8443/source", 304),
    ]


@dataclass(frozen=True, slots=True)
class OrdinalReader:
    bodies: tuple[bytes, ...]

    def read(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> TrustedSourcePayload:
        del actor
        return TrustedSourcePayload(
            logical_source_ref=attachment.logical_source_ref,
            logical_revision_ref=attachment.logical_revision_ref,
            data=self.bodies[attachment.ordinal],
            mime_type=attachment.mime_type,
            sanitized_locator=f"upload-{attachment.ordinal}.txt",
        )


def test_multi_attachment_delivery_commits_every_ordered_unit_before_receipt(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    event = _event(kind=ConversationEventKind.ATTACHMENT_RECEIVED, text="")
    attachments = tuple(
        AttachmentCapability(
            ordinal=ordinal,
            logical_source_ref=f"upload.{ordinal}",
            logical_revision_ref=f"upload.{ordinal}.rev1",
            mime_type="text/plain",
            capability_ref=f"capability.{ordinal}",
        )
        for ordinal in (0, 1)
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.multi",
        event_kind=event.event_kind,
        request_text="",
        attachments=attachments,
        timestamp=NOW,
    )

    # When
    receipt = KnowledgeIngestion(
        repository,
        attachment_reader=OrdinalReader((b"first", b"second")),
    ).ingest(actor, event, envelope)

    # Then
    assert tuple((unit.kind, unit.ordinal) for unit in receipt.unit_receipts) == (
        (IngestUnitKind.ATTACHMENT, 0),
        (IngestUnitKind.ATTACHMENT, 1),
    )
    with closing(sqlite3.connect(repository.database_path)) as database:
        counts = tuple(
            _COUNT_ROW.validate_python(database.execute(query).fetchone())[0]
            for query in (
                "SELECT COUNT(*) FROM sources",
                "SELECT COUNT(*) FROM jobs",
                "SELECT COUNT(*) FROM index_outbox",
            )
        )
        events = _COUNT_ROW.validate_python(
            database.execute("SELECT COUNT(*) FROM conversation_events").fetchone()
        )
    assert counts == (2, 2, 2)
    assert events == (1,)


def test_changed_attachment_advances_logical_source_revision(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    actor = _actor()
    repository.register_actor(actor, MembershipRole.EDITOR)
    first_event = _event(kind=ConversationEventKind.ATTACHMENT_RECEIVED, text="")
    second_event = first_event.model_copy(update={"revision": 2, "sequence": 2})
    first_attachment = AttachmentCapability(
        ordinal=0,
        logical_source_ref="upload.revised",
        logical_revision_ref="upload.revised.rev1",
        mime_type="text/plain",
        capability_ref="capability.rev1",
    )
    second_attachment = first_attachment.model_copy(
        update={
            "logical_revision_ref": "upload.revised.rev2",
            "capability_ref": "capability.rev2",
        }
    )
    ingestion = KnowledgeIngestion(
        repository,
        attachment_reader=OrdinalReader((b"first",)),
    )
    first_envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.revision.first",
        event_kind=first_event.event_kind,
        request_text="",
        attachments=(first_attachment,),
        timestamp=NOW,
    )
    first = _only_unit(ingestion.ingest(actor, first_event, first_envelope))
    ingestion = KnowledgeIngestion(
        repository,
        attachment_reader=OrdinalReader((b"second",)),
    )

    # When
    second = _only_unit(
        ingestion.ingest(
            actor,
            second_event,
            first_envelope.model_copy(
                update={
                    "delivery_id": "delivery.revision.second",
                    "attachments": (second_attachment,),
                }
            ),
        )
    )

    # Then
    assert first.source_id == second.source_id
    assert first.source_revision_id != second.source_revision_id
    stored = repository.read_source(actor, second.source_id)
    assert stored is not None
    assert stored.source.revision == 2
    assert stored.body == b"second"
