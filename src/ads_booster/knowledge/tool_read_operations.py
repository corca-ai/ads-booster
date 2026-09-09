from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING

from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.retrieval import SearchRequest
from ads_booster.knowledge.tool_contracts import (
    ExplanationData,
    KnowledgeGetInput,
    KnowledgePageData,
    KnowledgeSearchData,
    KnowledgeSearchHit,
    KnowledgeSearchInput,
    MemoryData,
    MemoryExplainInput,
    MemoryGetInput,
    SkillData,
    SkillGetInput,
    SkillListData,
    SkillListInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.knowledge.tool_support import KnowledgeToolError, error_result, success

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_dependencies import ToolDependencies


def knowledge_search(
    dependencies: ToolDependencies,
    request: KnowledgeSearchInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    result = dependencies.retriever.search(
        context.actor,
        SearchRequest(
            query=request.query,
            corpus=request.corpus,
            attributes={item.key: item.value for item in request.attributes} or None,
            brand_id=context.brand_id,
            limit=request.limit,
        ),
        now=context.invoked_at,
    )
    filtered = (
        result.hits
        if request.title_or_alias is None
        else tuple(
            item
            for item in result.hits
            if request.title_or_alias.casefold() in item.title.casefold()
            or (
                item.kind.value == "wiki"
                and dependencies.state.page_title_alias_matches(
                    context.actor,
                    item.entity_id,
                    request.title_or_alias,
                )
            )
        )
    )
    data = KnowledgeSearchData(
        hits=tuple(
            KnowledgeSearchHit(
                rank=rank,
                kind=item.kind,
                entity_id=item.entity_id,
                revision_id=item.revision_id,
                citation_id=item.citation_id,
                title=item.title,
                snippet=item.snippet,
                relevance_micros=max(0, round(item.score * 1_000_000)),
                related_page_ids=item.related_page_ids,
                needs_review=item.needs_review,
                claim_id=item.claim_id,
                conflict_group_id=item.conflict_group_id,
                conflict_role=item.conflict_role,
            )
            for rank, item in enumerate(filtered, start=1)
        ),
        retrieval_status=result.status.value,
        index_pending=result.index_pending,
        total_count=len(filtered),
    )
    status = ToolResultStatus.NO_RESULTS if not filtered else ToolResultStatus.SUCCEEDED
    return success(context.invocation_id, status, data)


def knowledge_get(
    dependencies: ToolDependencies,
    request: KnowledgeGetInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    stored = dependencies.repository.read_page(context.actor, request.page_id, request.revision_id)
    if stored is None:
        return error_result(
            context.invocation_id,
            "knowledge_page_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    selected = stored.revision
    if request.claim_ids:
        available = {item.claim_id: item for item in selected.claims}
        if any(claim_id not in available for claim_id in request.claim_ids):
            raise KnowledgeToolError("knowledge_claim_not_found")
        selected = selected.model_copy(
            update={"claims": tuple(available[item] for item in request.claim_ids)}
        )
    return success(
        context.invocation_id,
        ToolResultStatus.SUCCEEDED,
        KnowledgePageData(page=stored.page, revision=selected, markdown=stored.body.decode()),
    )


def memory_get(
    dependencies: ToolDependencies,
    request: MemoryGetInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    document_id = dependencies.state.find_memory_document_id(
        context.actor,
        request.kind,
        request.brand_id,
        request.local_date,
    )
    if document_id is None:
        return error_result(
            context.invocation_id,
            "memory_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    stored = dependencies.repository.read_memory(context.actor, document_id, request.revision_id)
    if stored is None:
        return error_result(
            context.invocation_id,
            "memory_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    visible_entries = tuple(
        item
        for item in stored.entries
        if item.dependency_state.value == "current"
        and item.status.value in {"active", "contested"}
        and (item.expires_at is None or item.expires_at > context.invoked_at)
    )
    snapshot = MemorySnapshot(
        document=stored.document,
        revision=stored.revision,
        entries=visible_entries,
        body=stored.body,
    )
    return success(
        context.invocation_id,
        ToolResultStatus.SUCCEEDED,
        MemoryData(
            document=snapshot.document,
            revision=snapshot.revision,
            entries=snapshot.entries,
            markdown="\n\n".join(item.text for item in snapshot.entries),
            view_pending=snapshot.revision.revision_id
            in dependencies.repository.pending_memory_views(context.actor.workspace_id),
        ),
    )


def memory_explain(
    dependencies: ToolDependencies,
    request: MemoryExplainInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    explanation = dependencies.state.explain(
        context.actor,
        request.target_id,
        request.task_receipt_id,
    )
    if explanation is None:
        return error_result(
            context.invocation_id,
            "explanation_target_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    return success(
        context.invocation_id,
        ToolResultStatus.SUCCEEDED,
        ExplanationData(explanation=explanation),
    )


def skill_list(
    dependencies: ToolDependencies,
    request: SkillListInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    entries = dependencies.skills.list(
        context.actor,
        request.applicability,
        include_protected=request.include_protected,
    )
    return success(
        context.invocation_id,
        ToolResultStatus.NO_RESULTS if not entries else ToolResultStatus.SUCCEEDED,
        SkillListData(entries=entries),
    )


def skill_get(
    dependencies: ToolDependencies,
    request: SkillGetInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    selected = dependencies.skills.get(context.actor, request.skill_id, request.revision_id)
    if selected is None:
        return error_result(
            context.invocation_id,
            "skill_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    return success(
        context.invocation_id,
        ToolResultStatus.SUCCEEDED,
        SkillData(
            record=selected.record,
            markdown=selected.markdown,
            display_pending=selected.display_pending,
            effective=selected.effective,
            override_status=selected.override_status,
        ),
    )


__all__ = [
    "knowledge_get",
    "knowledge_search",
    "memory_explain",
    "memory_get",
    "skill_get",
    "skill_list",
]
