"""Shared feedback corrections retain existing task and review-store boundaries."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_memory import (
    LegacyMemoryAssessment,
    LegacyMemoryAssessmentKind,
    MemoryAccess,
    MemoryNote,
    MemoryScope,
)
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.contracts import (
    CorrectionScope,
    CorrectionStatus,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard
from ads_booster.knowledge.tool_contracts import (
    CorrectionData,
    MemoryApplyInput,
    MemoryCorrectInput,
    MemoryRevisionPayload,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from ads_booster.learning.memory import SQLiteMemoryStore
from tests.knowledge.change_test_fixtures import (
    NOW,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
)
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from pathlib import Path

    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def _publish_core(host: ToolHost, curation: CurationInput) -> MemoryEntry:
    repository, processor, job, event, _ = curation
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    document = memory_document(kind=MemoryKind.CORE, document_id="memory.core")
    entry = memory_entry(document=document, kind=MemoryEntryKind.DECISION).model_copy(
        update={
            "entry_id": "entry.core.feedback",
            "text": event.text,
            "source_refs": (authenticated.evidence_ref,),
            "authority_ref": authenticated.authority_ref,
        }
    )
    document, revision, entries, body = memory_snapshot_parts(document=document, entries=(entry,))
    result = host.execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.core.seed",
            changes=(
                MemoryRevisionPayload(
                    operation=MemoryOperation(
                        operation_id="operation.core.seed",
                        kind=MemoryOperationKind.ADD,
                        document_id=document.document_id,
                        entry_id=entry.entry_id,
                        expected_revision_id="none",
                        reason="Seed the source-linked CORE target.",
                        evidence_refs=(authenticated.evidence_ref.evidence_id,),
                    ),
                    document=document,
                    revision=revision,
                    entries=entries,
                    body=body.decode(),
                ),
            ),
        ).model_dump(mode="json", by_alias=True),
        work.trusted_context,
    )
    assert result.status is ToolResultStatus.APPLIED
    assert repository.read_memory(processor.actor, document.document_id) is not None
    return entry


def _correction_input(
    *,
    scope: CorrectionScope,
    task_id: str | None = None,
    expected_revision_id: str | None = None,
    replacement_entry: MemoryEntry | None = None,
) -> MemoryCorrectInput:
    return MemoryCorrectInput(
        schema="knowledge.tool.memory-correct.v1",
        operation_id=f"operation.correction.{scope.value}",
        target_ids=("entry.core.feedback",),
        correction_text="Use the current Korean price of 31,000 won in this procedure.",
        scope=scope,
        task_id=task_id,
        authenticated_event_ref="message.input",
        expected_revision_id=expected_revision_id,
        replacement_entry=replacement_entry,
    )


correction_input = _correction_input
publish_core = _publish_core


def test_core_correction_applies_without_routine_question(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    original = _publish_core(host, curation_input)
    replacement = original.model_copy(update={"text": "The current Korean price is 31,000 won."})
    context = processor.build_curation_work(job).trusted_context

    # When
    result = host.execute(
        "memory_correct",
        _correction_input(
            scope=CorrectionScope.TEAM,
            expected_revision_id="memory.core.r1",
            replacement_entry=replacement,
        ).model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    stored = repository.read_memory(processor.actor, "memory.core")
    assert result.status is ToolResultStatus.APPLIED
    assert isinstance(result.data, CorrectionData)
    assert result.data.status is CorrectionStatus.APPLIED
    assert result.data.question_id is None
    assert stored is not None
    assert stored.revision.revision_id != "memory.core.r1"
    assert stored.entries == (replacement,)


def test_task_only_stays_overlay(curation_input: CurationInput, tmp_path: Path) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    original = _publish_core(host, curation_input)
    context = processor.build_curation_work(job).trusted_context
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=context.actor.workspace_id,
            product_id="trace",
            work_id=context.run_id,
        ),
        actor_id=context.actor.actor_id,
        can_review=True,
    )
    store = SQLiteMemoryStore(tmp_path / "task-only-service.sqlite3")
    note = MemoryNote(
        note_id="legacy.task-only.shared",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Keep the shared launch price unchanged.",
        source_ref="reviewer:task-only",
        source_sha256="d" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.put(note, access, now=NOW)
    review = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(note),
        stage="review",
        now=NOW,
    )
    _ = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(review),
        stage="approved",
        now=NOW,
    )
    selection = store.select(access, query="shared launch", run_id=context.run_id, now=NOW)
    task = TaskBinding(
        task_id="task.feedback",
        workspace_id=context.actor.workspace_id,
        actor_ref=context.actor.actor_id,
        member_id=context.actor.member_id,
        session_id=context.actor.session_id,
        action_kind=KnowledgeActionKind.TEAM_CHAT,
        capability_epoch=context.capability_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=NOW,
    )
    _ = host.open_task(context.actor, task)
    task_context = context.model_copy(
        update={"task_id": task.task_id, "legacy_memory_selection": selection, "invoked_at": NOW}
    )
    host = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store))

    # When
    result = host.execute(
        "memory_correct",
        _correction_input(scope=CorrectionScope.TASK_ONLY, task_id=task.task_id).model_dump(
            mode="json", by_alias=True
        ),
        task_context,
    )

    # Then
    stored = repository.read_memory(processor.actor, "memory.core")
    overlays = host.active_task_overlays(context.actor, task.task_id)
    assert result.status is ToolResultStatus.APPLIED
    assert isinstance(result.data, CorrectionData)
    assert result.data.status is CorrectionStatus.TASK_ONLY
    assert result.data.overlay is not None
    assert stored is not None
    assert stored.revision.revision_id == "memory.core.r1"
    assert stored.entries == (original,)
    assert overlays == (result.data.overlay,)


def test_legacy_review_note_is_not_auto_approved(
    curation_input: CurationInput, tmp_path: Path
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    original = _publish_core(host, curation_input)
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="workspace.alpha", product_id="trace"),
        actor_id="actor.editor",
        can_review=True,
    )
    note = MemoryNote(
        note_id="legacy.review.note",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Keep this only in the manual review queue.",
        source_ref="reviewer:legacy",
        source_sha256="a" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store = SQLiteMemoryStore(tmp_path / "legacy-memory.sqlite")
    store.put(note, access, now=NOW)
    replacement = original.model_copy(update={"text": "The current Korean price is 31,000 won."})
    context = processor.build_curation_work(job).trusted_context

    # When
    result = host.execute(
        "memory_correct",
        _correction_input(
            scope=CorrectionScope.TEAM,
            expected_revision_id="memory.core.r1",
            replacement_entry=replacement,
        ).model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    assert result.status is ToolResultStatus.APPLIED
    assert store.get(note.note_id, access) == note
    assert store.list_notes(access, stage="candidate") == (note,)


def test_approved_legacy_conflict_holds_core_head_and_creates_bound_question(
    curation_input: CurationInput, tmp_path: Path
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    original = _publish_core(ToolHost(repository), curation_input)
    context = processor.build_curation_work(job).trusted_context
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=context.actor.workspace_id,
            product_id="trace",
            work_id=context.run_id,
        ),
        actor_id=context.actor.actor_id,
        can_review=True,
    )
    store = SQLiteMemoryStore(tmp_path / "service.sqlite3")
    candidate = MemoryNote(
        note_id="legacy.approved.price",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Keep the approved Korean price at 29,000 won.",
        source_ref="reviewer:legacy-price",
        source_sha256="a" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.put(candidate, access, now=NOW)
    review = store.review(
        candidate.note_id,
        access,
        expected_sha256=contract_sha256(candidate),
        stage="review",
        now=NOW,
    )
    approved = store.review(
        candidate.note_id,
        access,
        expected_sha256=contract_sha256(review),
        stage="approved",
        now=NOW,
    )
    selection = store.select(access, query="Korean price", run_id=context.run_id, now=NOW)
    guarded_context = context.model_copy(
        update={"legacy_memory_selection": selection, "invoked_at": NOW}
    )
    host = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store))
    replacement = original.model_copy(update={"text": "The current Korean price is 31,000 won."})
    reference = selection.receipt.selected[0]
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    request = _correction_input(
        scope=CorrectionScope.TEAM,
        expected_revision_id="memory.core.r1",
        replacement_entry=replacement,
    ).model_copy(
        update={
            "legacy_memory_assessments": (
                LegacyMemoryAssessment(
                    selection_sha256=fingerprint,
                    reference=reference,
                    assessment=LegacyMemoryAssessmentKind.CONFLICT,
                ),
            )
        }
    )

    # When
    result = host.execute(
        "memory_correct",
        request.model_dump(mode="json", by_alias=True),
        guarded_context,
    )

    # Then
    stored = repository.read_memory(processor.actor, "memory.core")
    assert result.status is ToolResultStatus.PENDING
    assert isinstance(result.data, CorrectionData)
    assert result.data.status is CorrectionStatus.PENDING
    assert result.data.question_id is not None
    assert stored is not None
    assert stored.revision.revision_id == "memory.core.r1"
    question = host.state.question(context.actor, result.data.question_id)
    assert question is not None
    assert context.source_fetch_event is not None
    assert question.legacy_memory_conflicts == (reference,)
    assert question.legacy_memory_receipt == selection.receipt
    assert store.get(approved.note_id, access) == approved
    assert host.questions.pending_for_conversation(
        context.source_fetch_event.conversation_id,
        guarded_context,
    ) == (question,)
    store.delete(
        approved.note_id,
        access,
        expected_sha256=contract_sha256(approved),
        now=NOW,
    )
    assert (
        host.questions.pending_for_conversation(
            context.source_fetch_event.conversation_id,
            guarded_context,
        )
        == ()
    )
