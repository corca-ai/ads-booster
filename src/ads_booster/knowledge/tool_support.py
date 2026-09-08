from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never, override

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import GrantCapability
from ads_booster.knowledge.grant_policy import authorize_read, authorize_schedule, authorize_write
from ads_booster.knowledge.tool_contracts import (
    KnowledgeApplyInput,
    KnowledgeGetInput,
    KnowledgeQuestionInput,
    KnowledgeScheduleInput,
    KnowledgeSearchInput,
    KnowledgeToolName,
    MemoryApplyInput,
    MemoryCorrectInput,
    MemoryExplainInput,
    MemoryGetInput,
    SourceFetchInput,
    SourceReadInput,
    SourceSearchInput,
    ToolData,
    ToolInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.transport.json_types import JsonObject

JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
TOOL_INPUT_ADAPTER: TypeAdapter[ToolInput] = TypeAdapter(ToolInput)


@dataclass(slots=True)
class KnowledgeToolError(Exception):
    code: str
    retryable: bool = False

    @override
    def __str__(self) -> str:
        return self.code


def request_tool_name(request: ToolInput) -> KnowledgeToolName:  # noqa: C901, PLR0911
    match request:
        case KnowledgeSearchInput():
            return KnowledgeToolName.KNOWLEDGE_SEARCH
        case KnowledgeGetInput():
            return KnowledgeToolName.KNOWLEDGE_GET
        case MemoryGetInput():
            return KnowledgeToolName.MEMORY_GET
        case MemoryApplyInput():
            return KnowledgeToolName.MEMORY_APPLY
        case MemoryExplainInput():
            return KnowledgeToolName.MEMORY_EXPLAIN
        case MemoryCorrectInput():
            return KnowledgeToolName.MEMORY_CORRECT
        case SourceReadInput():
            return KnowledgeToolName.SOURCE_READ
        case SourceSearchInput():
            return KnowledgeToolName.SOURCE_SEARCH
        case SourceFetchInput():
            return KnowledgeToolName.SOURCE_FETCH
        case KnowledgeApplyInput():
            return KnowledgeToolName.KNOWLEDGE_APPLY
        case KnowledgeScheduleInput():
            return KnowledgeToolName.KNOWLEDGE_SCHEDULE
        case KnowledgeQuestionInput():
            return KnowledgeToolName.KNOWLEDGE_QUESTION
    assert_never(request)


def required_capability(name: KnowledgeToolName) -> GrantCapability:
    match name:
        case (
            KnowledgeToolName.KNOWLEDGE_SEARCH
            | KnowledgeToolName.KNOWLEDGE_GET
            | KnowledgeToolName.MEMORY_GET
            | KnowledgeToolName.MEMORY_EXPLAIN
            | KnowledgeToolName.SOURCE_READ
            | KnowledgeToolName.SOURCE_SEARCH
            | KnowledgeToolName.KNOWLEDGE_QUESTION
        ):
            return GrantCapability.READ
        case (
            KnowledgeToolName.MEMORY_APPLY
            | KnowledgeToolName.MEMORY_CORRECT
            | KnowledgeToolName.SOURCE_FETCH
            | KnowledgeToolName.KNOWLEDGE_APPLY
        ):
            return GrantCapability.WRITE
        case KnowledgeToolName.KNOWLEDGE_SCHEDULE:
            return GrantCapability.SCHEDULE
    assert_never(name)


def authorize_tool(name: KnowledgeToolName, context: TrustedInvocationContext) -> None:
    capability = required_capability(name)
    match capability:
        case GrantCapability.READ:
            _ = authorize_read(
                actor=context.actor,
                target_scope=context.actor.conversation_scope,
                at=context.invoked_at,
            )
        case GrantCapability.WRITE:
            _ = authorize_write(
                actor=context.actor,
                target_scope=context.actor.conversation_scope,
                at=context.invoked_at,
            )
        case GrantCapability.SCHEDULE:
            _ = authorize_schedule(
                actor=context.actor,
                target_scope=context.actor.conversation_scope,
                at=context.invoked_at,
            )
        case (
            GrantCapability.BRAND_VOICE_EDIT
            | GrantCapability.SHARE
            | GrantCapability.PURGE
        ):
            raise KnowledgeToolError("tool_capability_unsupported")


def schema(name: KnowledgeToolName) -> JsonObject:  # noqa: C901
    match name:
        case KnowledgeToolName.KNOWLEDGE_SEARCH:
            raw = KnowledgeSearchInput.model_json_schema()
        case KnowledgeToolName.KNOWLEDGE_GET:
            raw = KnowledgeGetInput.model_json_schema()
        case KnowledgeToolName.MEMORY_GET:
            raw = MemoryGetInput.model_json_schema()
        case KnowledgeToolName.MEMORY_APPLY:
            raw = MemoryApplyInput.model_json_schema()
        case KnowledgeToolName.MEMORY_EXPLAIN:
            raw = MemoryExplainInput.model_json_schema()
        case KnowledgeToolName.MEMORY_CORRECT:
            raw = MemoryCorrectInput.model_json_schema()
        case KnowledgeToolName.SOURCE_READ:
            raw = SourceReadInput.model_json_schema()
        case KnowledgeToolName.SOURCE_SEARCH:
            raw = SourceSearchInput.model_json_schema()
        case KnowledgeToolName.SOURCE_FETCH:
            raw = SourceFetchInput.model_json_schema()
        case KnowledgeToolName.KNOWLEDGE_APPLY:
            raw = KnowledgeApplyInput.model_json_schema()
        case KnowledgeToolName.KNOWLEDGE_SCHEDULE:
            raw = KnowledgeScheduleInput.model_json_schema()
        case KnowledgeToolName.KNOWLEDGE_QUESTION:
            raw = KnowledgeQuestionInput.model_json_schema()
    return JSON_OBJECT_ADAPTER.validate_python(raw)


def success(operation_id: str, status: ToolResultStatus, data: ToolData) -> ToolResult:
    return ToolResult(
        schema="knowledge.tool-result.v1",
        status=status,
        data=data,
        operation_id=operation_id,
    )


def error_result(
    operation_id: str,
    code: str,
    *,
    status: ToolResultStatus = ToolResultStatus.REJECTED,
    retryable: bool = False,
) -> ToolResult:
    return ToolResult(
        schema="knowledge.tool-result.v1",
        status=status,
        error_code=code,
        retryable=retryable,
        operation_id=operation_id,
    )


__all__ = [
    "TOOL_INPUT_ADAPTER",
    "KnowledgeToolError",
    "authorize_tool",
    "error_result",
    "request_tool_name",
    "required_capability",
    "schema",
    "success",
]
