"""Durable Slack delivery intent for an unresolved shared learning question."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.knowledge.contract_types import ScopeKind

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_contracts import QuestionRecord


class SlackLearningQuestionIntent(ContractModel):
    """A source-thread-bound question that may be delivered once through Slack."""

    schema_version: Literal["trace.slack-learning-question-intent.v1"] = (
        "trace.slack-learning-question-intent.v1"
    )
    intent_id: BoundedId
    question_id: BoundedId
    workspace_id: BoundedId
    conversation_id: BoundedId
    source_event_id: BoundedId
    text: str


@dataclass(frozen=True, slots=True)
class SlackLearningQuestionAnswer:
    """An explicit question-addressed reply parsed from a Slack message."""

    question_id: str
    answer: str


_QUESTION_ANSWER = re.compile(r"^(question\.[A-Za-z0-9._-]+)\s*:\s*(.+)$", re.DOTALL)
_AMBIGUOUS_AFFIRMATIONS = frozenset({"yes", "y", "네", "예", "응", "맞아"})
_CORRECTION_TERMS = (
    r"\b(?:correction|conflict|incorrect|wrong|instead|do not|don't)\b",
    r"수정",
    r"고쳐",
    r"바꿔",
    r"틀렸",
    r"아니",
    r"하지\s*마",
    r"않",
)
_CORRECTION_SIGNAL = re.compile(rf"(?:{'|'.join(_CORRECTION_TERMS)})")


def learning_question_intent(question: QuestionRecord) -> SlackLearningQuestionIntent | None:
    """Project one source-bound shared question into an idempotent Slack intent."""
    if (
        question.source_event_id is None
        or question.source_conversation_id is None
        or question.source_scope is None
        or question.source_scope.kind is not ScopeKind.WORKSPACE
    ):
        return None
    identity = contract_sha256(
        {
            "workspace_id": question.workspace_id,
            "question_id": question.question_id,
            "conversation_id": question.source_conversation_id,
            "source_event_id": question.source_event_id,
        }
    )
    return SlackLearningQuestionIntent(
        intent_id=f"slack-learning-question-{identity[:40]}",
        question_id=question.question_id,
        workspace_id=question.workspace_id,
        conversation_id=question.source_conversation_id,
        source_event_id=question.source_event_id,
        text=(
            f"확인이 필요합니다 ({question.question_id}).\n{question.problem}\n\n"
            f"권장: {question.recommendation}\n"
            f"이 스레드에서 `{question.question_id}: 답변` 형식으로 알려주세요."
        ),
    )


def learning_question_answer(text: str) -> SlackLearningQuestionAnswer | None:
    """Return only a reply that names its stable learning question identifier."""
    match = _QUESTION_ANSWER.fullmatch(text.strip())
    if match is None:
        return None
    return SlackLearningQuestionAnswer(question_id=match.group(1), answer=match.group(2).strip())


def is_ambiguous_learning_affirmation(text: str) -> bool:
    """Recognize a bare affirmative that needs a pending-question disambiguation."""
    return text.strip().casefold() in _AMBIGUOUS_AFFIRMATIONS


def has_learning_correction_signal(text: str) -> bool:
    """Gate summary-only urgent curation; the provider still decides whether to apply."""
    return _CORRECTION_SIGNAL.search(text.casefold()) is not None


__all__ = [
    "SlackLearningQuestionAnswer",
    "SlackLearningQuestionIntent",
    "has_learning_correction_signal",
    "is_ambiguous_learning_affirmation",
    "learning_question_answer",
    "learning_question_intent",
]
