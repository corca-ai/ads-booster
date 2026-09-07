from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ads_booster.knowledge.change_publication import ChangePublisher
    from ads_booster.knowledge.corrections import KnowledgeCorrections
    from ads_booster.knowledge.ingestion import KnowledgeIngestion
    from ads_booster.knowledge.questions import KnowledgeQuestions
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_tool_state import RepositoryToolState
    from ads_booster.knowledge.retrieval import KnowledgeRetriever
    from ads_booster.knowledge.web_search import SourceSearch


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    repository: SqliteKnowledgeRepository
    state: RepositoryToolState
    ingestion: KnowledgeIngestion
    retriever: KnowledgeRetriever
    publisher: ChangePublisher
    source_search: SourceSearch | None
    questions: KnowledgeQuestions
    corrections: KnowledgeCorrections


__all__ = ["ToolDependencies"]
