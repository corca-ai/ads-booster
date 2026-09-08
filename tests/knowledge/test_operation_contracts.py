from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ads_booster.knowledge.contracts import (
    AccessScope,
    CorrectionIntent,
    CorrectionScope,
    CorrectionStatus,
    CurationBatch,
    CurationTarget,
    EventReceipt,
    JobKind,
    JobPriority,
    JobState,
    KnowledgeJob,
    MemoryOperation,
    MemoryOperationKind,
    OperationReceipt,
    OperationStatus,
    ScopeKind,
)

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


def _scope() -> AccessScope:
    return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.team-a")


def test_correction_intent_preserves_task_and_brand_binding() -> None:
    intent = CorrectionIntent(
        schema="knowledge.correction-intent.v1",
        intent_id="correction.1",
        request_id="request.correct.1",
        actor_ref="member.a1",
        workspace_id="workspace.team-a",
        scope=CorrectionScope.TASK_ONLY,
        task_ref="task.logical.t1",
        brand_ref="brand.a",
        target_ids=("entry.voice.1",),
        correction_text="Use a question title in this task.",
        authenticated_event_ref="event.correct.1",
        status=CorrectionStatus.PENDING,
        policy_epoch=7,
        created_at=NOW,
    )

    assert intent.task_ref == "task.logical.t1"
    assert intent.brand_ref == "brand.a"


def test_task_only_correction_requires_task_reference() -> None:
    with pytest.raises(ValidationError, match="task_correction_requires_task"):
        _ = CorrectionIntent(
            schema="knowledge.correction-intent.v1",
            intent_id="correction.1",
            request_id="request.correct.1",
            actor_ref="member.a1",
            workspace_id="workspace.team-a",
            scope=CorrectionScope.TASK_ONLY,
            target_ids=("entry.voice.1",),
            correction_text="Use a question title in this task.",
            authenticated_event_ref="event.correct.1",
            status=CorrectionStatus.PENDING,
            policy_epoch=7,
            created_at=NOW,
        )


def test_job_and_batch_represent_plan_states_without_claiming_completion() -> None:
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id="job.curation.1",
        workspace_id="workspace.team-a",
        scope=_scope(),
        kind=JobKind.CURATION,
        state=JobState.WAITING_DEPENDENCY,
        priority=JobPriority.ROUTINE,
        root_event_id="event.1",
        policy_version="policy.v7",
        due_at=NOW,
        created_at=NOW,
        reason_code="batch_assignment",
    )
    batch = CurationBatch(
        schema="knowledge.curation-batch.v1",
        batch_id="batch.1",
        workspace_id="workspace.team-a",
        scope=_scope(),
        priority=JobPriority.ROUTINE,
        policy_version="policy.v7",
        read_grant_sha256="a" * 64,
        write_capability_sha256="b" * 64,
        first_event_at=NOW,
        batch_deadline=NOW + timedelta(seconds=60),
        event_receipts=(
            EventReceipt(
                event_id="event.1",
                event_revision=1,
                status=OperationStatus.PENDING,
            ),
        ),
    )

    assert job.state is JobState.WAITING_DEPENDENCY
    assert batch.event_receipts[0].status is OperationStatus.PENDING


def test_memory_operation_and_receipt_bind_expected_revision() -> None:
    operation = MemoryOperation(
        operation_id="operation.memory.1",
        kind=MemoryOperationKind.SUPERSEDE,
        document_id="memory.core.1",
        entry_id="entry.old.1",
        expected_revision_id="memory.core.rev1",
        replacement_entry_id="entry.new.1",
        reason="The source was corrected.",
        evidence_refs=("source.price.kr.rev2",),
    )
    receipt = OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation.operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=("memory.core.rev2",),
        retryable=False,
        occurred_at=NOW,
    )

    assert operation.expected_revision_id == "memory.core.rev1"
    assert receipt.status is OperationStatus.APPLIED


def test_batch_deadline_precedes_first_event_is_invalid() -> None:
    with pytest.raises(ValidationError, match="batch_deadline_precedes_first_event"):
        _ = CurationBatch(
            schema="knowledge.curation-batch.v1",
            batch_id="batch.1",
            workspace_id="workspace.team-a",
            scope=_scope(),
            priority=JobPriority.ROUTINE,
            policy_version="policy.v7",
            read_grant_sha256="a" * 64,
            write_capability_sha256="b" * 64,
            first_event_at=NOW,
            batch_deadline=NOW - timedelta(seconds=1),
        )


def test_curation_targets_are_bounded_and_unique() -> None:
    with pytest.raises(ValidationError, match="curation_targets_must_be_unique"):
        _ = EventReceipt(
            event_id="event.1",
            event_revision=1,
            status=OperationStatus.PENDING,
            targets=(CurationTarget.WIKI, CurationTarget.WIKI),
        )
