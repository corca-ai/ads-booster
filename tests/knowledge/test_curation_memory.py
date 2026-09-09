from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.contract_types import (
    GrantCapability,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    SourceDisposition,
    UsageRole,
)
from ads_booster.knowledge.contracts import AccessScope, ConversationEventKind, ScopeKind
from ads_booster.knowledge.curation_contracts import CurationMemoryIntent
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.curation_memory_payload import existing_subject_entry
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.indexing import KnowledgeIndexWorker
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import MemoryOperationKind
from ads_booster.knowledge.repository import JobClaim, MembershipRole
from ads_booster.knowledge.repository_types import StoredMemory
from ads_booster.knowledge.source_contracts import QuotedSpan
from ads_booster.knowledge.tool_contracts import (
    KnowledgeSearchData,
    KnowledgeSearchInput,
    MemoryApplyInput,
    MemoryRevisionPayload,
    ToolResultStatus,
)
from ads_booster.knowledge.tool_support import KnowledgeToolError
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import (
    authority,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    wiki_summary_ref,
)
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from ads_booster.knowledge.batch_curation import CurationBatchWork
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def test_factual_intent_persists_canonical_memory(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    intent = CurationMemoryIntent(
        subject_key="한국 팀",
        text="확정 가격은 31,000원입니다.",
        evidence_ids=(event.message_id,),
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        intent,
        work.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert len(stored.entries) == 1
    assert "한국 팀" in stored.entries[0].text
    assert "31,000" in stored.entries[0].text


def test_replay_keeps_one_revision(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    intent = CurationMemoryIntent(
        subject_key="budget", text="31000", evidence_ids=(event.message_id,)
    )
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    assert (
        writer.write(work.request, intent, work.trusted_context).status is ToolResultStatus.APPLIED
    )
    original = repository.read_memory(processor.actor, "memory.core")
    # When
    result = writer.write(work.request, intent, work.trusted_context)
    # Then
    assert result.status is ToolResultStatus.REPLAYED
    assert repository.read_memory(processor.actor, "memory.core") == original


def test_forged_evidence_cannot_create_memory(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    item = work.request.conversation_evidence[0]
    forged = item.model_copy(
        update={
            "evidence": item.evidence.model_copy(
                update={
                    "evidence_ref": item.evidence.evidence_ref.model_copy(
                        update={"quote_sha256": "a" * 64}
                    ),
                }
            )
        }
    )
    request = work.request.model_copy(update={"conversation_evidence": (forged,)})
    intent = CurationMemoryIntent(
        subject_key="budget", text="31000", evidence_ids=(event.message_id,)
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        request,
        intent,
        work.trusted_context,
    )
    # Then
    assert result.error_code == "curation_memory_evidence_binding_mismatch"
    assert repository.read_memory(processor.actor, "memory.core") is None


def test_private_actor_cannot_write_workspace_memory(curation_input: CurationInput) -> None:
    # Given

    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    scope = AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id=processor.actor.workspace_id,
        member_id=processor.actor.member_id,
        session_id=processor.actor.session_id,
    )
    context = work.trusted_context.model_copy(
        update={
            "actor": processor.actor.model_copy(update={"conversation_scope": scope}),
        }
    )
    intent = CurationMemoryIntent(
        subject_key="budget", text="31000", evidence_ids=(event.message_id,)
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request, intent, context
    )
    # Then
    assert result.error_code == "curation_memory_requires_shared_scope"
    assert repository.read_memory(processor.actor, "memory.core") is None


def test_correction_replaces_fact_and_preserves_other_entry_and_history(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    for subject, text in (("한국 팀", "가격은 31,000원"), ("지원 언어", "한국어")):
        intent = CurationMemoryIntent(
            subject_key=subject, text=text, evidence_ids=(event.message_id,)
        )
        assert (
            writer.write(work.request, intent, work.trusted_context).status
            is ToolResultStatus.APPLIED
        )
    original = repository.read_memory(processor.actor, "memory.core")
    assert original is not None
    corrected = _correction_work(curation_input)
    intent = CurationMemoryIntent(
        subject_key="한국 팀",
        text="가격은 41,000원",
        evidence_ids=("message.correction",),
    )
    # When
    result = writer.write(corrected.request, intent, corrected.trusted_context)
    # Then
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert len(stored.entries) == 2
    assert "31,000" not in stored.body.decode()
    assert "41,000" in stored.entries[0].text
    assert {ref.evidence_id for ref in stored.entries[0].source_refs} == {
        event.message_id,
        "message.correction",
    }
    assert stored.entries[1] == original.entries[1]
    history = repository.read_memory(processor.actor, "memory.core", original.revision.revision_id)
    assert history is not None
    assert history.revision == original.revision
    assert history.entries == original.entries
    assert history.body == original.body


def _correction_work(curation_input: CurationInput) -> CurationBatchWork:

    repository, processor, _, event, _ = curation_input
    correction = event.model_copy(
        update={
            "message_id": "message.correction",
            "text": "가격을 41,000원으로 정정합니다.",
            "created_at": event.created_at + timedelta(seconds=1),
        }
    )
    ingested = KnowledgeIngestion(repository).ingest(
        processor.actor,
        correction,
        envelope(correction, "delivery.correction"),
    )
    while True:
        lease = repository.claim_job(
            JobClaim(
                "worker.correction",
                correction.created_at,
                correction.created_at + timedelta(minutes=1),
            )
        )
        assert lease is not None
        if lease.job.job_id == ingested.unit_receipts[0].receipt.curation_job_id:
            return processor.build_curation_work(lease.job)


def test_new_actor_and_new_session_searches_latest_memory(curation_input: CurationInput) -> None:
    # Given

    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    intent = CurationMemoryIntent(
        subject_key="한국 팀", text="가격은 31,000원", evidence_ids=(event.message_id,)
    )
    assert (
        writer.write(work.request, intent, work.trusted_context).status is ToolResultStatus.APPLIED
    )
    correction = _correction_work(curation_input)
    intent = intent.model_copy(
        update={"text": "가격은 41,000원", "evidence_ids": ("message.correction",)}
    )
    assert (
        writer.write(correction.request, intent, correction.trusted_context).status
        is ToolResultStatus.APPLIED
    )
    reader = processor.actor.model_copy(
        update={
            "actor_id": "actor.new",
            "member_id": "member.new",
            "session_id": "session.new",
        }
    )
    repository.register_actor(reader, MembershipRole.ADMIN)
    context = work.trusted_context.model_copy(update={"actor": reader, "run_id": "run.new"})
    # When
    result = ToolHost(repository).execute(
        "knowledge_search",
        KnowledgeSearchInput(
            schema="knowledge.tool.search.v1",
            query="한국 팀",
        ).model_dump(mode="json", by_alias=True),
        context,
    )
    # Then
    assert result.status is ToolResultStatus.SUCCEEDED, result
    assert isinstance(result.data, KnowledgeSearchData)
    memory_hits = [hit for hit in result.data.hits if hit.kind.value == "memory"]
    assert len(memory_hits) == 1
    assert "41,000" in memory_hits[0].snippet
    assert "31,000" not in memory_hits[0].snippet


def test_old_event_cannot_replace_newer_fact(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    old = processor.build_curation_work(job)
    new = _correction_work(curation_input)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    intent = CurationMemoryIntent(
        subject_key="한국 팀", text="41000", evidence_ids=("message.correction",)
    )
    assert writer.write(new.request, intent, new.trusted_context).status is ToolResultStatus.APPLIED
    original = repository.read_memory(processor.actor, "memory.core")
    # When
    result = writer.write(
        old.request,
        intent.model_copy(
            update={
                "text": "31000",
                "evidence_ids": (event.message_id,),
            }
        ),
        old.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.CONFLICT
    assert result.error_code == "curation_memory_newer_evidence_exists"
    assert repository.read_memory(processor.actor, "memory.core") == original


def test_changed_source_revision_rejects_stale_work(curation_input: CurationInput) -> None:
    # Given

    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    edited = event.model_copy(
        update={
            "revision": 2,
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": event.created_at + timedelta(seconds=1),
            "text": "최신 수정",
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor, edited, envelope(edited, "delivery.edit")
    )
    intent = CurationMemoryIntent(
        subject_key="한국 팀", text="31000", evidence_ids=(event.message_id,)
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request, intent, work.trusted_context
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "curation_memory_evidence_binding_mismatch"
    assert repository.read_memory(processor.actor, "memory.core") is None


def test_published_source_is_admitted_and_index_queue_drains(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, receipt = curation_input
    work = processor.build_curation_work(job)
    intent = CurationMemoryIntent(
        subject_key="한국 팀",
        text="가격은 31,000원",
        evidence_ids=(event.message_id,),
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        intent,
        work.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.APPLIED
    source = repository.read_source(processor.actor, receipt.source_id)
    assert source is not None
    assert source.source.disposition is SourceDisposition.ADMIT
    worker = KnowledgeIndexWorker(repository)
    outcomes: list[str] = []
    for _ in range(10):
        indexed = worker.run_once(
            processor.actor.workspace_id, "worker.index", work.trusted_context.invoked_at
        )
        outcomes.append(indexed.state)
        if indexed.state == "idle":
            break
    assert outcomes[-1] == "idle"
    assert "failed" not in outcomes
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM index_outbox WHERE state != 'completed'"
            ).fetchone()[0]
            == 0
        )
    result = ToolHost(repository).execute(
        "knowledge_search",
        KnowledgeSearchInput(
            schema="knowledge.tool.search.v1",
            query="한국 팀",
        ).model_dump(mode="json", by_alias=True),
        work.trusted_context,
    )
    assert isinstance(result.data, KnowledgeSearchData)
    assert not result.data.index_pending


def test_replay_requires_current_write_grant(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    intent = CurationMemoryIntent(
        subject_key="budget", text="31000", evidence_ids=(event.message_id,)
    )
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    assert (
        writer.write(work.request, intent, work.trusted_context).status is ToolResultStatus.APPLIED
    )
    reader = processor.actor.model_copy(
        update={
            "grants": tuple(
                grant
                for grant in processor.actor.grants
                if grant.capability is GrantCapability.READ
            )
        }
    )
    # When
    result = writer.write(
        work.request, intent, work.trusted_context.model_copy(update={"actor": reader})
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code is not None


def test_unprovided_evidence_cannot_extend_memory(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    intent = CurationMemoryIntent(
        subject_key="budget",
        text="31000",
        evidence_ids=(event.message_id, "forged.event"),
    )
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        intent,
        work.trusted_context,
    )
    # Then
    assert result.error_code == "curation_memory_evidence_not_provided"
    assert repository.read_memory(processor.actor, "memory.core") is None


@pytest.mark.parametrize("change", ["edited", "quoted", "deleted"])
def test_changed_retained_evidence_is_rejected(
    curation_input: CurationInput,
    change: str,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    intent = CurationMemoryIntent(
        subject_key="한국 팀", text="31000", evidence_ids=(event.message_id,)
    )
    assert (
        writer.write(work.request, intent, work.trusted_context).status is ToolResultStatus.APPLIED
    )
    edited = event.model_copy(
        update={
            "revision": 2,
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": event.created_at + timedelta(microseconds=1),
            "text": "수정된 최초 조건",
        }
    )
    if change == "quoted":
        edited = edited.model_copy(
            update={
                "quoted_spans": (QuotedSpan(start=0, end=2, source_message_id="quoted.original"),),
            }
        )
    if change == "deleted":
        edited = edited.model_copy(
            update={"event_kind": ConversationEventKind.MESSAGE_DELETED, "text": ""}
        )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor, edited, envelope(edited, "delivery.changed")
    )
    correction = _correction_work(curation_input)
    # When
    result = writer.write(
        correction.request,
        intent.model_copy(
            update={
                "text": "41000",
                "evidence_ids": ("message.correction",),
            }
        ),
        correction.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "curation_memory_previous_evidence_stale"


@pytest.mark.parametrize("document_id", ["memory.core", "memory.custom.core"])
def test_existing_subject_preserves_manually_created_entry_identity(
    curation_input: CurationInput,
    document_id: str,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    assert work.request.authenticated_user_event is not None
    entry = memory_entry(
        document=memory_document(kind=MemoryKind.CORE, document_id=document_id),
        entry_id="entry.manual",
        kind=MemoryEntryKind.FACT,
    ).model_copy(
        update={
            "text": "한국 팀 가격은 31000",
            "applicability": AppliesTo(
                subject_key=" 한국   팀 ",
                task_ref="task.original",
                product_refs=("product.original",),
                action_kinds=(KnowledgeActionKind.CONTENT_WRITE,),
            ),
            "expires_at": work.trusted_context.invoked_at + timedelta(days=30),
            "review_after": work.trusted_context.invoked_at + timedelta(days=10),
            "source_refs": (work.request.authenticated_user_event.evidence_ref,),
        }
    )
    document, revision, entries, body = memory_snapshot_parts(
        document=memory_document(kind=MemoryKind.CORE, document_id=document_id),
        entries=(entry,),
    )
    payload = MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id="operation.manual",
        changes=(
            MemoryRevisionPayload(
                operation=MemoryOperation(
                    operation_id="operation.manual",
                    kind=MemoryOperationKind.ADD,
                    document_id=document_id,
                    entry_id=entry.entry_id,
                    expected_revision_id="none",
                    reason="Pre-existing limited applicability fact",
                    evidence_refs=(entry.source_refs[0].evidence_id,),
                ),
                document=document,
                revision=revision,
                entries=entries,
                body=body.decode(),
            ),
        ),
    )
    host = ToolHost(repository)
    assert (
        host.execute(
            "memory_apply", payload.model_dump(mode="json", by_alias=True), work.trusted_context
        ).status
        is ToolResultStatus.APPLIED
    )
    correction = _correction_work(curation_input)
    # When
    result = CurationMemoryWriter(repository, host).write(
        correction.request,
        CurationMemoryIntent(
            subject_key="한국 팀",
            text="41000",
            evidence_ids=("message.correction",),
        ),
        correction.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repository.read_memory(processor.actor, document_id)
    assert stored is not None
    assert len(stored.entries) == 1
    assert stored.entries[0].entry_id == "entry.manual"
    assert "41000" in stored.entries[0].text
    assert stored.entries[0].expires_at == entry.expires_at
    assert stored.entries[0].review_after == entry.review_after
    assert stored.entries[0].applicability == entry.applicability
    assert stored.entries[0].scope == entry.scope


def test_current_revision_can_replace_retained_stale_reference(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    old = processor.build_curation_work(job)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    intent = CurationMemoryIntent(
        subject_key="한국 팀", text="31000", evidence_ids=(event.message_id,)
    )
    assert writer.write(old.request, intent, old.trusted_context).status is ToolResultStatus.APPLIED
    edited = event.model_copy(
        update={
            "revision": 2,
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": event.created_at + timedelta(microseconds=1),
            "text": "수정된 최초 조건",
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor, edited, envelope(edited, "delivery.revised")
    )
    correction = _correction_work(curation_input)
    # When
    result = writer.write(
        correction.request,
        intent.model_copy(
            update={
                "text": "41000",
                "evidence_ids": (event.message_id, "message.correction"),
            }
        ),
        correction.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert len(stored.entries) == 1
    retained = next(
        ref for ref in stored.entries[0].source_refs if ref.evidence_id == event.message_id
    )
    assert retained.revision_id == "2"
    assert retained.quote_sha256 == sha256(edited.text.encode()).hexdigest()


@pytest.mark.parametrize("protected", ["wiki_summary", "constraint"])
def test_subject_resolution_rejects_protected_memory(protected: str) -> None:
    # Given
    document = memory_document(kind=MemoryKind.CORE, document_id="memory.core")
    entry = memory_entry(document=document, kind=MemoryEntryKind.FACT).model_copy(
        update={
            "applicability": AppliesTo(subject_key="한국 팀"),
        }
    )
    if protected == "wiki_summary":
        entry = entry.model_copy(
            update={
                "origin": MemoryOrigin.WIKI_SUMMARY,
                "wiki_ref": wiki_summary_ref(),
            }
        )
    if protected == "constraint":
        entry = entry.model_copy(
            update={"usage_role": UsageRole.CONSTRAINT, "authority_ref": authority()}
        )
    document, revision, entries, body = memory_snapshot_parts(document=document, entries=(entry,))
    stored = StoredMemory(document, revision, entries, body)
    # When / Then
    with pytest.raises(KnowledgeToolError, match="curation_memory_subject_protected"):
        _ = existing_subject_entry(stored, "한국 팀")
