from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.contracts import (
    ConversationRole,
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.messages import MessageValidationError
from ads_booster.knowledge.repository import JobClaim
from ads_booster.knowledge.source_contracts import QuotedSpan
from ads_booster.knowledge.tool_contracts import (
    MemoryApplyInput,
    MemoryRevisionPayload,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import (
    NOW,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    register_evidence_source,
)
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("forged", [False, True])
def test_canonical_user_authority_applies_direct_team_decision(
    curation_input: CurationInput, forged: bool
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    host = ToolHost(repository)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    ref = authenticated.evidence_ref
    authority = authenticated.authority_ref
    assert ref.evidence_id == event.message_id
    assert ref.revision_id == str(event.revision)
    if forged:
        authority = authority.model_copy(update={"actor_ref": "member.forged"})
    document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    entry = memory_entry(document=document, kind=MemoryEntryKind.DECISION).model_copy(
        update={"source_refs": (ref,), "text": event.text, "authority_ref": authority}
    )
    document, revision, entries, body = memory_snapshot_parts(document=document, entries=(entry,))
    request = MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id="operation.memory.input",
        changes=(
            MemoryRevisionPayload(
                operation=MemoryOperation(
                    operation_id="operation.memory.input",
                    kind=MemoryOperationKind.ADD,
                    document_id=document.document_id,
                    entry_id=entry.entry_id,
                    expected_revision_id="none",
                    reason="Preserve source observation",
                    evidence_refs=(ref.evidence_id,),
                ),
                document=document,
                revision=revision,
                entries=entries,
                body=body.decode(),
            ),
        ),
    )
    # When
    result = host.execute(
        "memory_apply", request.model_dump(mode="json", by_alias=True), work.trusted_context
    )
    # Then
    stored = repository.read_memory(processor.actor, document.document_id)
    if forged:
        assert result.error_code == "decision_authority_binding_mismatch"
        assert stored is None
    else:
        assert result.status is ToolResultStatus.APPLIED, result
        assert stored is not None
        assert stored.entries[0].source_refs == (ref,)
        assert stored.entries[0].text == event.text


def test_quoted_message_does_not_receive_user_authority(curation_input: CurationInput) -> None:
    # Given
    repository, processor, _, original, _ = curation_input
    event = original.model_copy(
        update={
            "message_id": "message.quoted",
            "quoted_spans": (
                QuotedSpan(start=0, end=len(original.text), source_message_id="message.external"),
            ),
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor, event, envelope(event, "delivery.quoted")
    )
    lease = repository.claim_job(JobClaim("worker.quoted", NOW, NOW + timedelta(minutes=1)))
    assert lease is not None
    # When
    work = processor.build_curation_work(lease.job)
    # Then
    assert work.request.authenticated_user_event is None
    assert work.request.excerpts[0].text == event.text


def test_reference_file_does_not_receive_user_authority(curation_input: CurationInput) -> None:
    # Given
    repository, processor, _, _, _ = curation_input
    _ = register_evidence_source(repository, body=b"External reference")
    lease = repository.claim_job(JobClaim("worker.reference", NOW, NOW + timedelta(minutes=1)))
    assert lease is not None
    # When
    work = processor.build_curation_work(lease.job)
    # Then
    assert work.request.authenticated_user_event is None


def test_assistant_message_cannot_enter_authenticated_curation(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, _, original, _ = curation_input
    event = original.model_copy(
        update={"message_id": "message.assistant", "role": ConversationRole.ASSISTANT}
    )
    # When / Then
    with pytest.raises(MessageValidationError, match="independent_non_user_evidence_forbidden"):
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor, event, envelope(event, "delivery.assistant")
        )
