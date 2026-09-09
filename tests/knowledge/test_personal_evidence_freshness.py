from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.contracts import ConversationEventKind, IngestEnvelope, MessageEventRef
from ads_booster.knowledge.errors import EvidenceResolutionError
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.source_contracts import QuotedSpan
from ads_booster.knowledge.tool_contracts import MemoryApplyInput, ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.personal_publication_fixtures import (
    NOW,
    ingest_personal_event,
    personal_actor,
    personal_context,
    user_payload,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.contracts import ActorContext, ConversationEvent


def _revise(
    repository: SqliteKnowledgeRepository,
    owner: ActorContext,
    event: ConversationEvent,
    kind: ConversationEventKind,
    *,
    quoted: bool = False,
) -> ConversationEvent:
    text = "" if kind is ConversationEventKind.MESSAGE_DELETED else event.text
    revision = event.model_copy(
        update={
            "revision": 2,
            "sequence": 2,
            "event_kind": kind,
            "edited_at": NOW + timedelta(seconds=1),
            "text": text,
            "quoted_spans": (
                QuotedSpan(start=0, end=len(text), source_message_id="message.somebody-else"),
            )
            if quoted
            else (),
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        owner,
        revision,
        IngestEnvelope(
            schema="knowledge.ingest-envelope.v1",
            delivery_id="delivery.revised",
            event_kind=kind,
            request_text=text,
            message_event=MessageEventRef(
                conversation_ref=event.conversation_id,
                message_ref=event.message_id,
                revision=revision.revision,
            ),
            timestamp=NOW + timedelta(seconds=1),
        ),
    )
    return revision


def test_manual_user_apply_rejects_quoted_preference(tmp_path: Path) -> None:
    # Given a current canonical owner message containing somebody else's quoted preference.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    original, _ = ingest_personal_event(repository, owner)
    event = _revise(repository, owner, original, ConversationEventKind.MESSAGE_EDITED, quoted=True)
    payload = user_payload(owner, event)
    context = personal_context(owner).model_copy(update={"invoked_at": NOW + timedelta(seconds=2)})
    # When the generic memory_apply path attempts to publish it as an owner preference.
    result = ToolHost(repository).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        context,
    )
    # Then canonical authorship does not turn quoted text into an owner declaration.
    assert result.status is ToolResultStatus.REJECTED, result
    assert result.error_code == "user_memory_owner_evidence_required"
    assert repository.read_memory(owner, payload.document.document_id) is None


@pytest.mark.parametrize(
    "kind", [ConversationEventKind.MESSAGE_EDITED, ConversationEventKind.MESSAGE_DELETED]
)
@pytest.mark.parametrize("replay", [False, True])
def test_manual_user_apply_rejects_historical_event_after_edit_or_delete(
    tmp_path: Path, kind: ConversationEventKind, replay: bool
) -> None:
    # Given an original owner preference, optionally already published under this operation ID.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    original, _ = ingest_personal_event(repository, owner)
    payload = user_payload(owner, original)
    arguments = MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id=payload.operation.operation_id,
        changes=(payload,),
    ).model_dump(mode="json", by_alias=True)
    host = ToolHost(repository)
    if replay:
        assert (
            host.execute("memory_apply", arguments, personal_context(owner)).status
            is ToolResultStatus.APPLIED
        )
    _ = _revise(repository, owner, original, kind)
    before = repository.read_memory(owner, payload.document.document_id)
    context = personal_context(owner).model_copy(update={"invoked_at": NOW + timedelta(seconds=2)})
    # When an old request tries to publish/replay using the superseded canonical event revision.
    result = host.execute("memory_apply", arguments, context)
    # Then current revision validation rejects resurrection before mutation or replay acceptance.
    assert result.status is ToolResultStatus.REJECTED, result
    expected_code = (
        "missing_evidence"
        if kind is ConversationEventKind.MESSAGE_DELETED
        else "user_memory_owner_evidence_required"
    )
    assert result.error_code == expected_code
    assert result.retryable is False
    assert repository.read_memory(owner, payload.document.document_id) == before
    # Edited history stays readable for audits; deleted evidence remains blocked.
    if kind is ConversationEventKind.MESSAGE_DELETED:
        with pytest.raises(EvidenceResolutionError):
            _ = repository.resolve_evidence(owner, payload.entries[0].source_refs[0])
    else:
        assert repository.resolve_evidence(owner, payload.entries[0].source_refs[0]) == original


def test_manual_user_apply_accepts_current_unquoted_owner_revision(tmp_path: Path) -> None:
    # Given the current edited revision of the owner's unquoted channel message.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    original, _ = ingest_personal_event(repository, owner)
    current = _revise(repository, owner, original, ConversationEventKind.MESSAGE_EDITED)
    payload = user_payload(owner, current)
    context = personal_context(owner).model_copy(update={"invoked_at": NOW + timedelta(seconds=2)})
    # When generic publication references that exact current event.
    result = ToolHost(repository).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        context,
    )
    # Then the owner preference is accepted with the current revision retained.
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repository.read_memory(owner, payload.document.document_id)
    assert stored is not None
    assert stored.entries[0].source_refs[0].revision_id == "2"
