from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, final

from pydantic import ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.corrections import CorrectionError, KnowledgeCorrections
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.ingestion_sources import IngestionError
from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard, LegacyMemoryGuardError
from ads_booster.knowledge.questions import KnowledgeQuestions, QuestionAnswerResult, QuestionError
from ads_booster.knowledge.repository_tool_state import RepositoryToolState, ToolStateError
from ads_booster.knowledge.repository_types import RepositoryConflictError
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.skills import KnowledgeSkills
from ads_booster.knowledge.source_fetch import SourceFetchError
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
    ToolCatalogEntry,
    ToolInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
    TrustedQuestionAnswer,
)
from ads_booster.knowledge.tool_dependencies import ToolDependencies
from ads_booster.knowledge.tool_read_operations import (
    knowledge_get,
    knowledge_search,
    memory_explain,
    memory_get,
)
from ads_booster.knowledge.tool_source_operations import source_fetch, source_read, source_search
from ads_booster.knowledge.tool_support import (
    TOOL_INPUT_ADAPTER,
    KnowledgeToolError,
    authorize_tool,
    error_result,
    request_tool_name,
    required_capability,
    schema,
)
from ads_booster.knowledge.tool_write_operations import (
    knowledge_apply,
    knowledge_question,
    knowledge_schedule,
    memory_apply,
    memory_correct,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.governance_contracts import TaskBinding, TaskOverlay
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.web_search import SourceSearch
    from ads_booster.transport.json_types import JsonObject


@final
class ToolHost:
    def __init__(  # noqa: D107, PLR0913
        self,
        repository: SqliteKnowledgeRepository,
        *,
        ingestion: KnowledgeIngestion | None = None,
        retriever: KnowledgeRetriever | None = None,
        publisher: ChangePublisher | None = None,
        source_search: SourceSearch | None = None,
        legacy_memory: LegacyMemoryGuard | None = None,
    ) -> None:
        state = RepositoryToolState(repository)
        questions = KnowledgeQuestions(state, legacy_memory)
        self._dependencies = ToolDependencies(
            repository=repository,
            state=state,
            ingestion=ingestion or KnowledgeIngestion(repository),
            retriever=retriever or KnowledgeRetriever(repository),
            publisher=publisher or ChangePublisher(repository, adoption_resolver=state),
            source_search=source_search,
            questions=questions,
            corrections=KnowledgeCorrections(state, questions, legacy_memory),
            skills=KnowledgeSkills(repository),
            legacy_memory=legacy_memory,
        )

    def execute(  # noqa: C901, PLR0911
        self,
        name: str,
        arguments: JsonObject,
        trusted_context: TrustedInvocationContext,
    ) -> ToolResult:
        try:
            tool_name = KnowledgeToolName(name)
        except ValueError:
            return error_result(trusted_context.invocation_id, "tool_name_unknown")
        try:
            request = TOOL_INPUT_ADAPTER.validate_python(arguments)
        except ValidationError:
            return error_result(trusted_context.invocation_id, "tool_input_invalid")
        if request_tool_name(request) is not tool_name:
            return error_result(trusted_context.invocation_id, "tool_input_schema_mismatch")
        try:
            authorize_tool(tool_name, trusted_context)
            return self._execute(request, trusted_context)
        except KnowledgeToolError as error:
            return error_result(
                trusted_context.invocation_id,
                error.code,
                retryable=error.retryable,
            )
        except (CorrectionError, LegacyMemoryGuardError, QuestionError, ToolStateError) as error:
            return error_result(trusted_context.invocation_id, error.code)
        except KnowledgePolicyError as error:
            return error_result(trusted_context.invocation_id, error.code)
        except RepositoryConflictError as error:
            return error_result(
                trusted_context.invocation_id,
                error.code,
                status=ToolResultStatus.CONFLICT,
            )
        except ChangeValidationError as error:
            return error_result(trusted_context.invocation_id, error.code)
        except IngestionError as error:
            return error_result(trusted_context.invocation_id, error.code)
        except SourceFetchError as error:
            return error_result(
                trusted_context.invocation_id,
                error.code,
                retryable=error.retryable,
            )
        except UnicodeDecodeError:
            return error_result(trusted_context.invocation_id, "source_decode_failed")

    def schemas(self) -> dict[KnowledgeToolName, JsonObject]:
        return {name: schema(name) for name in KnowledgeToolName}

    def catalog(self) -> tuple[ToolCatalogEntry, ...]:
        return tuple(
            ToolCatalogEntry(
                name=name,
                input_schema_sha256=contract_sha256(item_schema),
                required_capability=required_capability(name),
            )
            for name, item_schema in self.schemas().items()
        )

    def open_task(self, actor: ActorContext, binding: TaskBinding) -> TaskBinding:
        return self._dependencies.state.open_task(actor, binding)

    def close_task(
        self,
        actor: ActorContext,
        task_id: str,
        capability_epoch: int,
        closed_at: datetime,
    ) -> TaskBinding:
        return self._dependencies.state.close_task(actor, task_id, capability_epoch, closed_at)

    def active_task_overlays(
        self,
        actor: ActorContext,
        task_id: str,
    ) -> tuple[TaskOverlay, ...]:
        return self._dependencies.state.active_task_overlays(actor, task_id)

    @property
    def questions(self) -> KnowledgeQuestions:
        return self._dependencies.questions

    def answer_question(
        self,
        answer: TrustedQuestionAnswer,
        trusted_context: TrustedInvocationContext,
    ) -> QuestionAnswerResult:
        return self._dependencies.questions.answer(answer, trusted_context)

    @property
    def state(self) -> RepositoryToolState:
        return self._dependencies.state

    def _execute(  # noqa: C901, PLR0911, PLR0912
        self,
        request: ToolInput,
        context: TrustedInvocationContext,
    ) -> ToolResult:
        dependencies = self._dependencies
        match request:
            case KnowledgeSearchInput():
                return knowledge_search(dependencies, request, context)
            case KnowledgeGetInput():
                return knowledge_get(dependencies, request, context)
            case MemoryGetInput():
                return memory_get(dependencies, request, context)
            case MemoryApplyInput():
                return memory_apply(dependencies, request, context)
            case MemoryExplainInput():
                return memory_explain(dependencies, request, context)
            case MemoryCorrectInput():
                return memory_correct(dependencies, request, context)
            case SourceReadInput():
                return source_read(dependencies, request, context)
            case SourceSearchInput():
                return source_search(dependencies, request, context)
            case SourceFetchInput():
                return source_fetch(dependencies, request, context)
            case KnowledgeApplyInput():
                return knowledge_apply(dependencies, request, context)
            case KnowledgeScheduleInput():
                return knowledge_schedule(dependencies, request, context)
            case KnowledgeQuestionInput():
                return knowledge_question(dependencies, request, context)
        assert_never(request)


__all__ = ["KnowledgeToolError", "ToolHost"]
