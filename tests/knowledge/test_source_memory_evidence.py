from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.change_evidence import RepositoryEvidenceResolver
from ads_booster.knowledge.contracts import (
    ConversationEventKind,
    EvidenceKind,
    EvidenceRef,
    MemoryEntryKind,
    MemoryOperation,
    MemoryOperationKind,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.tool_contracts import (
    MemoryApplyInput,
    MemoryRevisionPayload,
    SourceReadData,
    SourceReadInput,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import memory_entry, memory_snapshot_parts
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("historical", [False, True])
def test_source_evidence_resolves_extracted_korean_quote(
    curation_input: CurationInput, historical: bool
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    excerpt = work.request.excerpts[0]
    assert excerpt.segment_id is not None
    if historical:
        updated = event.model_copy(
            update={
                "revision": 2,
                "event_kind": ConversationEventKind.MESSAGE_EDITED,
                "edited_at": event.created_at + timedelta(seconds=1),
                "text": "후속 수정 내용",
            }
        )
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor, updated, envelope(updated, "delivery.historical")
        )
    ref = EvidenceRef(
        evidence_kind=EvidenceKind.SOURCE_SEGMENT,
        evidence_id=excerpt.segment_id,
        segment_id=excerpt.segment_id,
        revision_id=excerpt.revision_id,
        quote_sha256=sha256(event.text.encode()).hexdigest(),
        scope=processor.actor.conversation_scope,
    )
    entry = memory_entry(kind=MemoryEntryKind.OBSERVATION).model_copy(
        update={"source_refs": (ref,)}
    )
    # When
    catalog = RepositoryEvidenceResolver(repository).validation_catalog(
        actor=processor.actor, entries=(entry,), brand_id=None
    )
    # Then
    assert catalog.evidence[0].quote == event.text


@pytest.mark.parametrize("forged", ["", "quote_sha256", "segment_id"])
def test_source_read_evidence_can_be_applied_without_inventing_ids(
    curation_input: CurationInput, forged: str
) -> None:
    # Given
    repository, processor, job, event, receipt = curation_input
    work = processor.build_curation_work(job)
    segment_id = work.request.excerpts[0].segment_id
    assert segment_id is not None
    host = ToolHost(repository)
    read = host.execute(
        "source_read",
        SourceReadInput(
            schema="knowledge.tool.source-read.v1",
            source_id=receipt.source_id,
            revision_id=receipt.source_revision_id,
            segment_ids=(segment_id,),
        ).model_dump(mode="json", by_alias=True),
        work.trusted_context,
    )
    assert isinstance(read.data, SourceReadData)
    ref = read.data.excerpts[0].evidence_ref
    assert ref is not None
    if forged:
        ref = ref.model_copy(update={forged: "0" * 64})
    entry = memory_entry(kind=MemoryEntryKind.OBSERVATION).model_copy(
        update={"source_refs": (ref,), "text": event.text}
    )
    document, revision, entries, body = memory_snapshot_parts(entries=(entry,))
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
        assert result.error_code == "evidence_pointer_mismatch"
        assert stored is None
    else:
        assert result.status is ToolResultStatus.APPLIED, result
        assert stored is not None
        assert stored.entries[0].source_refs == (ref,)
        assert stored.entries[0].text == event.text
