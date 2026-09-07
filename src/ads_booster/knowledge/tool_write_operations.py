from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_publication import ChangeGroup, MemoryPublication
from ads_booster.knowledge.grant_policy import authorize_schedule
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.operation_contracts import KnowledgeJob, KnowledgeOperation
from ads_booster.knowledge.operation_enums import JobState, OperationStatus
from ads_booster.knowledge.pages import PageChangeSet, PageSnapshot
from ads_booster.knowledge.repository_types import JobRegistration
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
    QuestionData,
    ScheduleData,
    ScheduledKnowledgeRequest,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.knowledge.tool_support import success

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_dependencies import ToolDependencies


def memory_apply(
    dependencies: ToolDependencies,
    request: MemoryApplyInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    return publish(
        dependencies,
        operation_id=request.operation_id,
        page_operation=None,
        pages=(),
        redirects=(),
        memory_changes=request.changes,
        adoption_receipt_ids=request.adoption_receipt_ids,
        context=context,
    )


def memory_correct(
    dependencies: ToolDependencies,
    request: MemoryCorrectInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    data = dependencies.corrections.correct(request, context)
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
]
