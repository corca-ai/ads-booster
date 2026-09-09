from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_publication import ChangeGroup, MemoryPublication
from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.grant_policy import authorize_schedule
from ads_booster.knowledge.legacy_memory import LegacyMemoryGuardError
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.memory_drafts import normalize_core_memory_draft
from ads_booster.knowledge.operation_contracts import KnowledgeJob, KnowledgeOperation
from ads_booster.knowledge.operation_enums import JobState, OperationStatus
from ads_booster.knowledge.pages import PageChangeSet, PageSnapshot
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_types import JobRegistration, RepositoryConflictError
from ads_booster.knowledge.skill_drafts import normalize_skill_operations
from ads_booster.knowledge.tool_contracts import (
    ApplyData,
    KnowledgeApplyInput,
    KnowledgeQuestionInput,
    KnowledgeScheduleInput,
    MemoryApplyInput,
    MemoryCorrectInput,
    MemoryRevisionPayload,
    PageRedirectPayload,
    PageRevisionPayload,
    PendingProposal,
    ProposalTargetKind,
    QuestionData,
    ScheduleData,
    ScheduledKnowledgeRequest,
    SkillApplyData,
    SkillApplyInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.knowledge.tool_support import success

if TYPE_CHECKING:
    from ads_booster.contracts.agent_memory import LegacyMemoryAssessment, MemoryReference
    from ads_booster.knowledge.tool_dependencies import ToolDependencies


def memory_apply(
    dependencies: ToolDependencies,
    request: MemoryApplyInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    conflicts = _legacy_conflicts(dependencies, context, request.legacy_memory_assessments)
    if conflicts:
        target_id = request.target_ids[0]
        expected_revision_id = (
            request.changes[0].operation.expected_revision_id
            if request.changes
            else request.drafts[0].expected_revision_id
        )
        return _hold_legacy_conflict(
            dependencies,
            context,
            operation_id=request.operation_id,
            target_kind=ProposalTargetKind.MEMORY,
            target_id=target_id,
            expected_revision_id=expected_revision_id,
            conflicts=conflicts,
        )
    if request.changes:
        changes = request.changes
    else:
        changes = (
            normalize_core_memory_draft(
                dependencies,
                request.drafts[0],
                context,
                request.operation_id,
            ),
        )
    return publish(
        dependencies,
        operation_id=request.operation_id,
        page_operation=None,
        pages=(),
        redirects=(),
        memory_changes=changes,
        adoption_receipt_ids=request.adoption_receipt_ids,
        context=context,
    )


def memory_correct(
    dependencies: ToolDependencies,
    request: MemoryCorrectInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    prepared = dependencies.corrections.correct(request, context)
    data = prepared.data
    if prepared.memory_change is not None:
        try:
            published = publish(
                dependencies,
                operation_id=request.operation_id,
                page_operation=None,
                pages=(),
                redirects=(),
                memory_changes=(prepared.memory_change,),
                adoption_receipt_ids=(),
                context=context,
            )
        except RepositoryConflictError as error:
            if error.code != "memory_head_conflict":
                raise
            held = dependencies.corrections.correct(request, context)
            if held.memory_change is not None:
                raise
            return success(request.operation_id, ToolResultStatus.PENDING, held.data)
        if published.status not in (ToolResultStatus.APPLIED, ToolResultStatus.REPLAYED):
            return published
        return success(
            request.operation_id,
            published.status,
            data.model_copy(update={"replayed": published.status is ToolResultStatus.REPLAYED}),
        )
    status = (
        ToolResultStatus.REPLAYED
        if data.replayed
        else ToolResultStatus.APPLIED
        if data.status.value == "task_only"
        else ToolResultStatus.PENDING
    )
    return success(request.operation_id, status, data)


def knowledge_apply(
    dependencies: ToolDependencies,
    request: KnowledgeApplyInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    if request.memory_changes:
        conflicts = _legacy_conflicts(dependencies, context, request.legacy_memory_assessments)
        if conflicts:
            change = request.memory_changes[0]
            return _hold_legacy_conflict(
                dependencies,
                context,
                operation_id=request.operation_id,
                target_kind=ProposalTargetKind.MEMORY,
                target_id=change.document.document_id,
                expected_revision_id=change.operation.expected_revision_id,
                conflicts=conflicts,
            )
    return publish(
        dependencies,
        operation_id=request.operation_id,
        page_operation=request.page_operation,
        pages=request.pages,
        redirects=request.redirects,
        memory_changes=request.memory_changes,
        adoption_receipt_ids=request.adoption_receipt_ids,
        context=context,
    )


def skill_apply(
    dependencies: ToolDependencies,
    request: SkillApplyInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    conflicts = _legacy_conflicts(dependencies, context, request.legacy_memory_assessments)
    if conflicts:
        operation = request.operations[0]
        return _hold_legacy_conflict(
            dependencies,
            context,
            operation_id=request.operation_id,
            target_kind=ProposalTargetKind.SKILL,
            target_id=operation.skill_id,
            expected_revision_id=operation.expected_revision_id or "none",
            conflicts=conflicts,
        )
    operations = normalize_skill_operations(
        dependencies,
        request.operations,
        context,
        request.operation_id,
    )
    receipt = dependencies.publisher.publish(
        actor=context.actor,
        group=ChangeGroup(
            operation_id=request.operation_id,
            skill_operations=operations,
        ),
        pages=None,
        memories=(),
        at=context.invoked_at,
        trusted_context=context,
    )
    match receipt.status:
        case OperationStatus.APPLIED:
            status = ToolResultStatus.APPLIED
        case OperationStatus.REPLAYED:
            status = ToolResultStatus.REPLAYED
        case OperationStatus.CONFLICT:
            status = ToolResultStatus.CONFLICT
        case OperationStatus.PENDING:
            status = ToolResultStatus.PENDING
        case OperationStatus.REJECTED | OperationStatus.FAILED:
            status = ToolResultStatus.REJECTED
    return success(
        request.operation_id,
        status,
        SkillApplyData(
            operation_id=request.operation_id,
            target_ids=tuple(operation.skill_id for operation in operations),
            receipt=receipt,
        ),
    )


def _legacy_conflicts(
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
    assessments: tuple[LegacyMemoryAssessment, ...],
) -> tuple[MemoryReference, ...]:
    guard = dependencies.legacy_memory
    if guard is None:
        if assessments:
            code = "legacy_memory_guard_unavailable"
            raise LegacyMemoryGuardError(code)
        return ()
    return guard.assess(context, assessments)


def _hold_legacy_conflict(  # noqa: PLR0913 - explicit typed proposal bindings.
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
    *,
    operation_id: str,
    target_kind: ProposalTargetKind,
    target_id: str,
    expected_revision_id: str,
    conflicts: tuple[MemoryReference, ...],
) -> ToolResult:
    source = context.source_fetch_event
    if source is None:
        code = "legacy_memory_conflict_source_missing"
        raise LegacyMemoryGuardError(code)
    selection = context.legacy_memory_selection
    if selection is None:
        code = "legacy_memory_selection_missing"
        raise LegacyMemoryGuardError(code)
    identity = contract_sha256(
        {
            "operation": operation_id,
            "selection": selection.receipt.selection_sha256,
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
        }
    )
    result = dependencies.questions.ask(
        KnowledgeQuestionInput(
            schema="knowledge.tool.question.v1",
            question_id=f"question.{identity[:32]}",
            problem="The proposed learning change conflicts with selected approved memory.",
            evidence_ids=(source.message_id,),
            checks_tried=("Compared every selected approved note against the proposed change.",),
            recommendation="Resolve the conflict before publishing this proposed change.",
            pending_proposal=PendingProposal(
                proposal_id=f"proposal.{operation_id}",
                target_kind=target_kind,
                target_id=target_id,
                expected_revision_id=expected_revision_id,
            ),
            source_event_id=source.message_id,
            legacy_memory_receipt=selection.receipt,
            legacy_memory_conflicts=conflicts,
        ),
        context,
    )
    return success(
        operation_id,
        ToolResultStatus.PENDING,
        QuestionData(question=result.question),
    )


def publish(  # noqa: PLR0913
    dependencies: ToolDependencies,
    *,
    operation_id: str,
    page_operation: KnowledgeOperation | None,
    pages: tuple[PageRevisionPayload, ...],
    redirects: tuple[PageRedirectPayload, ...],
    memory_changes: tuple[MemoryRevisionPayload, ...],
    adoption_receipt_ids: tuple[str, ...],
    context: TrustedInvocationContext,
) -> ToolResult:
    page_set = (
        None
        if not pages
        else PageChangeSet(
            current={
                item.page.page_id: PageSnapshot(
                    page=item.page,
                    revision=item.revision,
                    body=item.body.encode(),
                )
                for item in pages
            },
            redirects={item.from_page_id: item.to_page_id for item in redirects},
        )
    )
    memories = tuple(
        MemoryPublication(
            snapshot=MemorySnapshot(
                document=item.document,
                revision=item.revision,
                entries=item.entries,
                body=item.body.encode(),
            ),
            constraints=item.constraints,
        )
        for item in memory_changes
    )
    group = ChangeGroup(
        operation_id=operation_id,
        page_operation=page_operation,
        memory_operations=tuple(item.operation for item in memory_changes),
        adoption_receipt_ids=adoption_receipt_ids,
    )
    receipt = dependencies.publisher.publish(
        actor=context.actor,
        group=group,
        pages=page_set,
        memories=memories,
        at=context.invoked_at,
    )
    match receipt.status:
        case OperationStatus.APPLIED:
            status = ToolResultStatus.APPLIED
        case OperationStatus.REPLAYED:
            status = ToolResultStatus.REPLAYED
        case OperationStatus.CONFLICT:
            status = ToolResultStatus.CONFLICT
        case OperationStatus.PENDING:
            status = ToolResultStatus.PENDING
        case OperationStatus.REJECTED | OperationStatus.FAILED:
            status = ToolResultStatus.REJECTED
    return success(operation_id, status, ApplyData(receipt=receipt))


def knowledge_schedule(
    dependencies: ToolDependencies,
    request: KnowledgeScheduleInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    _ = authorize_schedule(
        actor=context.actor,
        target_scope=context.actor.conversation_scope,
        at=context.invoked_at,
    )
    unique_key = contract_sha256(
        {
            "workspace_id": context.actor.workspace_id,
            **(
                {"scope_key": scope_key(context.actor.conversation_scope)}
                if context.actor.conversation_scope.kind is ScopeKind.CHANNEL
                else {}
            ),
            "kind": request.kind.value,
            "targets": list(request.targets),
            "purpose": request.purpose,
            "triggers": list(request.triggers),
            "due_at": request.due_at.isoformat(),
            "epoch": context.capability_epoch,
        }
    )
    job_id = f"job.{unique_key[:32]}"
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id=job_id,
        workspace_id=context.actor.workspace_id,
        scope=context.actor.conversation_scope,
        submitter_actor=(
            context.actor if context.actor.conversation_scope.kind is ScopeKind.CHANNEL else None
        ),
        kind=request.kind,
        state=JobState.QUEUED,
        priority=request.priority,
        root_event_id=(
            context.source_fetch_event.message_id
            if context.source_fetch_event is not None
            else context.invocation_id
        ),
        policy_version=f"capability-epoch.{context.capability_epoch}",
        due_at=request.due_at,
        created_at=context.invoked_at,
        reason_code=request.purpose[:160],
    )
    replayed = dependencies.state.put_scheduled_job(
        context.actor,
        JobRegistration(job=job, unique_key=unique_key),
        ScheduledKnowledgeRequest(
            job_id=job_id,
            operation_id=request.operation_id,
            workspace_id=context.actor.workspace_id,
            targets=request.targets,
            purpose=request.purpose,
            triggers=request.triggers,
            capability_epoch=context.capability_epoch,
        ),
    )
    return success(
        request.operation_id,
        ToolResultStatus.REPLAYED if replayed else ToolResultStatus.APPLIED,
        ScheduleData(job_id=job_id, replayed=replayed),
    )


def knowledge_question(
    dependencies: ToolDependencies,
    request: KnowledgeQuestionInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    result = dependencies.questions.ask(request, context)
    return success(
        context.invocation_id,
        ToolResultStatus.REPLAYED if result.replayed else ToolResultStatus.PENDING,
        QuestionData(question=result.question),
    )


__all__ = [
    "knowledge_apply",
    "knowledge_question",
    "knowledge_schedule",
    "memory_apply",
    "memory_correct",
    "skill_apply",
]
