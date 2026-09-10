"""Approved legacy memory selections stay current and server owned."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_memory import (
    LegacyMemoryAssessment,
    LegacyMemoryAssessmentKind,
    MemoryAccess,
    MemoryNote,
    MemoryReference,
    MemoryScope,
    MemorySelection,
)
from ads_booster.contracts.agent_run import AgentRunState, contract_sha256
from ads_booster.knowledge.contracts import (
    CorrectionScope,
    KnowledgeJob,
    MemoryOperation,
    MemoryOperationKind,
    MemoryRevision,
)
from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard, LegacyMemoryGuardError
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from ads_booster.knowledge.skill_contracts import SkillApplyInput, SkillOperation
from ads_booster.knowledge.tool_contracts import (
    CorrectionData,
    KnowledgeApplyInput,
    MemoryApplyInput,
    MemoryRevisionPayload,
    QuestionData,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from ads_booster.learning.memory import SQLiteMemoryStore
from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.feedback_learning_support import (
    SharedMessage,
    admit_shared_source,
    learning_fixture,
)
from tests.knowledge.procedural_skill_test_support import skill_record
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_feedback_correction import (
    correction_input,
    publish_core,
)
from tests.marketing.agent_service.test_application import AskThenStopReasoning
from tests.marketing.channels.test_slack_events import receive
from tests.marketing.channels.test_slack_learning import installed_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


def test_current_selection_rejects_a_changed_approved_note(tmp_path: Path) -> None:
    # Given
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="workspace.alpha", product_id="trace", work_id="run.1"),
        actor_id="actor.editor",
        can_review=True,
    )
    note = MemoryNote(
        note_id="legacy.approved",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Use the approved launch procedure.",
        source_ref="reviewer:legacy",
        source_sha256="a" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=365),
    )
    store = SQLiteMemoryStore(tmp_path / "service.sqlite3")
    store.put(note, access, now=NOW)
    review = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(note),
        stage="review",
        now=NOW,
    )
    approved = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(review),
        stage="approved",
        now=NOW,
    )
    selected = store.select(access, query="approved launch", run_id="run.1", now=NOW)

    # When
    current = store.current_selection(access, selected.receipt, now=NOW)

    # Then
    assert current.notes == (approved,)
    assert current.receipt.actor_id == access.actor_id
    assert current.receipt.selection_sha256 is not None


def _approved_selection(
    tmp_path: Path,
    context: TrustedInvocationContext,
) -> tuple[SQLiteMemoryStore, MemoryAccess, MemoryNote, MemorySelection]:
    actor = context.actor
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=actor.workspace_id,
            product_id="trace",
            work_id=context.run_id,
        ),
        actor_id=actor.actor_id,
        can_review=True,
    )
    note = MemoryNote(
        note_id="legacy.approved",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Use the approved launch procedure.",
        source_ref="reviewer:legacy",
        source_sha256="a" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store = SQLiteMemoryStore(tmp_path / "service.sqlite3")
    store.put(note, access, now=NOW)
    review = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(note),
        stage="review",
        now=NOW,
    )
    approved = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(review),
        stage="approved",
        now=NOW,
    )
    selection = store.select(access, query="approved launch", run_id=context.run_id, now=NOW)
    return store, access, approved, selection


@pytest.mark.parametrize(
    "kind",
    [LegacyMemoryAssessmentKind.COMPATIBLE, LegacyMemoryAssessmentKind.UNRELATED],
)
def test_nonconflicting_assessments_clear_the_global_write_guard(
    curation_input: CurationInput,
    tmp_path: Path,
    kind: LegacyMemoryAssessmentKind,
) -> None:
    # Given
    _, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, _, _, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    guarded = context.model_copy(update={"legacy_memory_selection": selection})
    assessment = LegacyMemoryAssessment(
        selection_sha256=fingerprint,
        reference=selection.receipt.selected[0],
        assessment=kind,
    )

    # When
    conflicts = LegacyMemoryGuard(store).assess(guarded, (assessment,))

    # Then
    assert conflicts == ()


def test_zero_selected_legacy_notes_preserves_old_empty_assessment_path(
    curation_input: CurationInput,
    tmp_path: Path,
) -> None:
    # Given
    _, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store = SQLiteMemoryStore(tmp_path / "empty-service.sqlite3")
    guard = LegacyMemoryGuard(store)
    selection = guard.select(
        context.actor,
        run_id=context.run_id,
        query="no approved notes exist",
        now=NOW,
    )

    # When
    conflicts = guard.assess(
        context.model_copy(update={"legacy_memory_selection": selection}),
        (),
    )

    # Then
    assert selection.notes == ()
    assert conflicts == ()


@pytest.mark.parametrize(
    "kind",
    [LegacyMemoryAssessmentKind.COMPATIBLE, LegacyMemoryAssessmentKind.UNRELATED],
)
def test_nonconflicting_legacy_assessment_allows_silent_core_correction(
    curation_input: CurationInput,
    tmp_path: Path,
    kind: LegacyMemoryAssessmentKind,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    original = publish_core(ToolHost(repository), curation_input)
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, _, _, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    request = correction_input(
        scope=CorrectionScope.TEAM,
        expected_revision_id="memory.core.r1",
        replacement_entry=original.model_copy(update={"text": "Use 31,000 won."}),
    ).model_copy(
        update={
            "legacy_memory_assessments": (
                LegacyMemoryAssessment(
                    selection_sha256=fingerprint,
                    reference=selection.receipt.selected[0],
                    assessment=kind,
                ),
            )
        }
    )
    host = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store))

    # When
    result = host.execute(
        "memory_correct",
        request.model_dump(mode="json", by_alias=True),
        context.model_copy(update={"legacy_memory_selection": selection}),
    )

    # Then
    assert result.status is ToolResultStatus.APPLIED
    assert isinstance(result.data, CorrectionData)
    assert result.data.question_id is None
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert stored.revision.revision_id != "memory.core.r1"


def test_missing_or_digest_mismatched_assessment_rejects_global_write(
    curation_input: CurationInput,
    tmp_path: Path,
) -> None:
    # Given
    _, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, _, _, selection = _approved_selection(tmp_path, context)
    guarded = context.model_copy(update={"legacy_memory_selection": selection})
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    mismatched = LegacyMemoryAssessment(
        selection_sha256=fingerprint,
        reference=MemoryReference(note_id="legacy.approved", sha256="b" * 64),
        assessment=LegacyMemoryAssessmentKind.COMPATIBLE,
    )

    # When / Then
    with pytest.raises(LegacyMemoryGuardError, match="legacy_memory_assessment_incomplete"):
        _ = LegacyMemoryGuard(store).assess(guarded, ())
    with pytest.raises(LegacyMemoryGuardError, match="legacy_memory_assessment_mismatch"):
        _ = LegacyMemoryGuard(store).assess(guarded, (mismatched,))


@pytest.mark.parametrize(
    ("include_assessment", "reference_digest", "make_stale", "expected_error"),
    [
        (False, "", False, "legacy_memory_assessment_incomplete"),
        (True, "e" * 64, False, "legacy_memory_assessment_mismatch"),
        (True, "", True, "legacy_memory_selection_stale"),
    ],
)
def test_memory_correction_rejects_missing_mismatched_or_stale_legacy_assessment(  # noqa: PLR0913, PLR0917
    curation_input: CurationInput,
    tmp_path: Path,
    include_assessment: bool,
    reference_digest: str,
    make_stale: bool,
    expected_error: str,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    original = publish_core(ToolHost(repository), curation_input)
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, access, approved, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    selected_reference = selection.receipt.selected[0]
    reference = (
        selected_reference
        if not reference_digest
        else MemoryReference(note_id=approved.note_id, sha256=reference_digest)
    )
    assessments = (
        (
            LegacyMemoryAssessment(
                selection_sha256=fingerprint,
                reference=reference,
                assessment=LegacyMemoryAssessmentKind.COMPATIBLE,
            ),
        )
        if include_assessment
        else ()
    )
    if make_stale:
        store.delete(
            approved.note_id,
            access,
            expected_sha256=contract_sha256(approved),
            now=NOW,
        )
    request = correction_input(
        scope=CorrectionScope.TEAM,
        expected_revision_id="memory.core.r1",
        replacement_entry=original.model_copy(update={"text": "Use 31,000 won."}),
    ).model_copy(update={"legacy_memory_assessments": assessments})

    # When
    result = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store)).execute(
        "memory_correct",
        request.model_dump(mode="json", by_alias=True),
        context.model_copy(update={"legacy_memory_selection": selection}),
    )

    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == expected_error
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert stored.revision.revision_id == "memory.core.r1"


def test_stale_approved_note_rejects_assessment_and_other_actor_cannot_reuse_selection(
    curation_input: CurationInput,
    tmp_path: Path,
) -> None:
    # Given
    _, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, access, approved, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    assessment = LegacyMemoryAssessment(
        selection_sha256=fingerprint,
        reference=selection.receipt.selected[0],
        assessment=LegacyMemoryAssessmentKind.UNRELATED,
    )
    other_actor = context.actor.model_copy(update={"actor_id": "actor.other"})
    guard = LegacyMemoryGuard(store)

    with pytest.raises(LegacyMemoryGuardError, match="legacy_memory_selection_context_mismatch"):
        _ = guard.assess(
            context.model_copy(update={"actor": other_actor, "legacy_memory_selection": selection}),
            (assessment,),
        )
    with pytest.raises(LegacyMemoryGuardError, match="legacy_memory_selection_context_mismatch"):
        _ = guard.assess(
            context.model_copy(
                update={"run_id": "run.other", "legacy_memory_selection": selection}
            ),
            (assessment,),
        )
    store.delete(
        approved.note_id,
        access,
        expected_sha256=contract_sha256(approved),
        now=NOW,
    )

    # When / Then
    assert guard.latest(other_actor, run_id=context.run_id, now=NOW) is None
    with pytest.raises(LegacyMemoryGuardError, match="legacy_memory_selection_stale"):
        _ = guard.assess(
            context.model_copy(update={"legacy_memory_selection": selection}),
            (assessment,),
        )


def test_background_learning_request_reads_legacy_memory_from_persisted_source_run(
    tmp_path: Path,
) -> None:
    # Given
    fixture = learning_fixture(tmp_path / "learning")
    dependencies = batch_fixture(tmp_path / "dependencies")
    source_run_id = "run.persisted-learning-source"
    source = admit_shared_source(
        fixture,
        SharedMessage("message.persisted-learning-source", source_run_id),
    )
    admission = LearningReviewCoordinator(fixture.knowledge).admit_turn(
        source.binding,
        source.event,
        source.receipt,
        at=NOW,
        urgent=True,
    )
    assert admission.ready_batch_ids
    with fixture.knowledge.connection() as connection:
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                "SELECT job_json FROM jobs WHERE job_id=?",
                (source.receipt.curation_job_id,),
            ).fetchone()
        )
    assert row is not None
    job = KnowledgeJob.model_validate_json(row[0])
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=source.binding.actor.workspace_id,
            product_id="trace",
            work_id=source_run_id,
        ),
        actor_id=source.binding.actor.actor_id,
        can_review=True,
    )
    store = SQLiteMemoryStore(tmp_path / "background-service.sqlite3")
    note = MemoryNote(
        note_id="legacy.background.approved",
        scope=access.scope,
        category="preference",
        domain="learning",
        text="Use the approved cited workflow for the current launch.",
        source_ref="reviewer:background",
        source_sha256="c" * 64,
        author_id=access.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=365),
    )
    store.put(note, access, now=NOW)
    review = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(note),
        stage="review",
        now=NOW,
    )
    approved = store.review(
        note.note_id,
        access,
        expected_sha256=contract_sha256(review),
        stage="approved",
        now=NOW,
    )
    service_actor = source.binding.actor.model_copy(
        update={
            "actor_id": "actor.installed-service",
            "member_id": "member.installed-service",
            "session_id": "session.installed-service",
            "grants": tuple(
                grant.model_copy(update={"grant_id": f"service.{grant.grant_id}"})
                for grant in source.binding.actor.grants
            ),
        }
    )
    fixture.knowledge.register_actor(service_actor, MembershipRole.ADMIN)
    guarded_processor = CanonicalJobProcessor(
        fixture.knowledge,
        service_actor,
        dependencies.runtime.jobs.curation,
        dependencies.runtime.jobs.memory,
        legacy_memory=LegacyMemoryGuard(store),
    )

    # When
    work = guarded_processor.build_curation_work(job)

    # Then
    selection = work.request.legacy_memory_selection
    assert selection is not None
    assert selection.notes == (approved,)
    assert selection.receipt.run_id == source_run_id
    assert selection.receipt.actor_id == source.binding.actor.actor_id
    assert service_actor.actor_id != work.trusted_context.actor.actor_id
    assert work.trusted_context.actor.actor_id == source.binding.actor.actor_id
    assert work.trusted_context.legacy_memory_selection == selection
    assert work.trusted_context.run_id == source_run_id
    dependencies.close()


def test_declared_legacy_conflict_holds_skill_create_and_binds_external_note(
    curation_input: CurationInput,
    tmp_path: Path,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    context = work.trusted_context.model_copy(update={"invoked_at": NOW})
    store, _, approved, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    guarded = context.model_copy(update={"legacy_memory_selection": selection})
    record = skill_record(
        skill_id="learned.conflicting-launch",
        version="learned.conflicting-launch.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(authenticated.evidence_ref,),
    )
    operation = SkillOperation(
        operation_id="operation.skill.legacy-conflict",
        kind=SkillOperationKind.CREATE,
        skill_id=record.skill_id,
        replacement_revision_id=record.version,
        record=record,
        source_refs=record.source_refs,
        reason="Propose a procedure while retaining approved legacy context.",
    )
    request = SkillApplyInput(
        schema="knowledge.tool.skill-apply.v1",
        operation_id=operation.operation_id,
        operations=(operation,),
        legacy_memory_assessments=(
            LegacyMemoryAssessment(
                selection_sha256=fingerprint,
                reference=selection.receipt.selected[0],
                assessment=LegacyMemoryAssessmentKind.CONFLICT,
            ),
        ),
    )
    host = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store))

    # When
    result = host.execute(
        "skill_apply",
        request.model_dump(mode="json", by_alias=True),
        guarded,
    )

    # Then
    assert result.status is ToolResultStatus.PENDING
    assert isinstance(result.data, QuestionData)
    assert result.data.question.legacy_memory_conflicts == selection.receipt.selected
    assert repository.read_skill(processor.actor, record.skill_id) is None
    current_access = LegacyMemoryGuard.access(context.actor, run_id=context.run_id)
    assert store.get(approved.note_id, current_access) == approved


@pytest.mark.parametrize("use_combined_apply", [False, True])
def test_declared_legacy_conflict_fences_memory_apply_and_combined_apply(
    curation_input: CurationInput,
    tmp_path: Path,
    use_combined_apply: bool,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    original = publish_core(ToolHost(repository), curation_input)
    context = processor.build_curation_work(job).trusted_context.model_copy(
        update={"invoked_at": NOW}
    )
    store, _, _, selection = _approved_selection(tmp_path, context)
    fingerprint = selection.receipt.selection_sha256
    assert fingerprint is not None
    stored = repository.read_memory(processor.actor, "memory.core")
    assert stored is not None
    assert context.source_fetch_event is not None
    revision_id = "memory.core.legacy-guard-r2"
    replacement = original.model_copy(update={"text": "Use 31,000 won."})
    payload = MemoryRevisionPayload(
        operation=MemoryOperation(
            operation_id="operation.memory.legacy-conflict",
            kind=MemoryOperationKind.UPDATE,
            document_id=stored.document.document_id,
            entry_id=replacement.entry_id,
            expected_revision_id=stored.revision.revision_id,
            replacement_entry_id=replacement.entry_id,
            reason="Propose a global memory update against approved legacy context.",
            evidence_refs=(context.source_fetch_event.message_id,),
        ),
        document=stored.document.model_copy(update={"head_revision_id": revision_id}),
        revision=MemoryRevision(
            document_id=stored.document.document_id,
            revision_id=revision_id,
            previous_revision_id=stored.revision.revision_id,
            body_sha256=sha256(stored.body).hexdigest(),
            entry_ids=stored.revision.entry_ids,
            created_at=NOW,
        ),
        entries=(replacement,),
        body=stored.body.decode(),
    )
    assessment = LegacyMemoryAssessment(
        selection_sha256=fingerprint,
        reference=selection.receipt.selected[0],
        assessment=LegacyMemoryAssessmentKind.CONFLICT,
    )
    request = (
        KnowledgeApplyInput(
            schema="knowledge.tool.apply.v1",
            operation_id=payload.operation.operation_id,
            memory_changes=(payload,),
            legacy_memory_assessments=(assessment,),
        )
        if use_combined_apply
        else MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
            legacy_memory_assessments=(assessment,),
        )
    )
    tool_name = "knowledge_apply" if use_combined_apply else "memory_apply"

    # When
    result = ToolHost(repository, legacy_memory=LegacyMemoryGuard(store)).execute(
        tool_name,
        request.model_dump(mode="json", by_alias=True),
        context.model_copy(update={"legacy_memory_selection": selection}),
    )

    # Then
    assert result.status is ToolResultStatus.PENDING
    assert isinstance(result.data, QuestionData)
    current = repository.read_memory(processor.actor, "memory.core")
    assert current is not None
    assert current.revision.revision_id == stored.revision.revision_id


def test_slack_continuation_selection_binds_latest_u2_actor_and_canonical_workspace(
    tmp_path: Path,
) -> None:
    # Given
    owner, installed, _ = installed_events(tmp_path)
    owner.commands.application.service.reasoning = AskThenStopReasoning()
    try:
        receive(owner, user="U1")
        while owner.work_once(now=NOW):
            pass
        run = owner.commands.application.service.repository.list_runs("team")[0]
        assert run.state is AgentRunState.AWAITING_INPUT
        reviewer = MemoryAccess(
            scope=MemoryScope(
                workspace_id="team",
                channel_id="C1",
                product_id="trace",
                work_id=run.run_id,
            ),
            actor_id="actor.legacy-reviewer",
            can_review=True,
        )
        store = SQLiteMemoryStore(owner.store.database_path)
        note = MemoryNote(
            note_id="legacy.u2.current",
            scope=reviewer.scope,
            category="preference",
            domain="learning",
            text="Use the approved current continuation procedure.",
            source_ref="reviewer:u2",
            source_sha256="f" * 64,
            author_id=reviewer.actor_id,
            created_at=NOW,
            expires_at=NOW + timedelta(days=365),
        )
        store.put(note, reviewer, now=NOW)
        review = store.review(
            note.note_id,
            reviewer,
            expected_sha256=contract_sha256(note),
            stage="review",
            now=NOW,
        )
        approved = store.review(
            note.note_id,
            reviewer,
            expected_sha256=contract_sha256(review),
            stage="approved",
            now=NOW,
        )

        # When
        receive(
            owner,
            type="message",
            user="U2",
            text="Use the approved current continuation procedure.",
            ts="100.002",
            thread_ts="100.001",
        )
        while owner.work_once(now=NOW):
            pass

        # Then
        binding = installed.adapter.ingress.binding_for_run(run.run_id)
        assert binding is not None
        u2_identity = owner.identity("U2")
        assert binding.actor.member_id == u2_identity.member_id
        assert binding.actor.actor_id == u2_identity.member_id
        assert run.tenant_id == binding.actor.workspace_id == "team"
        guard = installed.adapter.legacy_memory
        assert guard is not None
        selection = guard.latest(binding.actor, run_id=run.run_id, now=NOW)
        assert selection is not None
        assert selection.notes == (approved,)
        assert selection.receipt.actor_id == binding.actor.actor_id
        assert selection.receipt.scope.workspace_id == binding.actor.workspace_id
        assert selection.receipt.scope.channel_id == binding.actor.conversation_scope.channel_id
        assert selection.receipt.scope.channel_id == "C1"
    finally:
        installed.runtime.close()
