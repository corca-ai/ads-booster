from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    CurationLimits,
    CurationObservation,
    CurationRequest,
    CurationRunStatus,
)
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.tool_contracts import (
    KnowledgeQuestionInput,
    KnowledgeToolName,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@dataclass(frozen=True, slots=True)
class QuestionProvider:
    first: str
    corrected: str
    expected_error: str

    def decide(
        self,
        request: CurationRequest,
        observations: tuple[CurationObservation, ...],
        *,
        timeout_seconds: float,
    ) -> CurationDecision:
        assert request.authenticated_user_event is not None
        assert timeout_seconds > 0
        if observations:
            assert observations[-1].result.error_code == self.expected_error
        return CurationDecision(
            schema="knowledge.curation-decision.v1",
            action=CurationDecisionAction.QUESTION,
            question_arguments_json=self.corrected if observations else self.first,
        )

    def decide_batch(
        self,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        return CurationBatchDecision(
            schema="knowledge.curation-batch-decision.v1",
            batch_id=batch_id,
            decisions=tuple(
                CurationBatchJobDecision(
                    job_id=item.request.job_id,
                    decision=self.decide(
                        item.request, item.observations, timeout_seconds=timeout_seconds
                    ),
                )
                for item in jobs
            ),
        )


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        ("missing_evidence", "question_evidence_not_found"),
        ("{", "curation_question_arguments_invalid"),
        ("{}", "tool_input_invalid"),
    ],
)
def test_question_input_error_can_be_corrected_before_waiting(
    curation_input: CurationInput, batch: bool, invalid: str, error: str
) -> None:
    # Given: a real source and a first question that cannot be stored.
    repository, processor, job, event, _ = curation_input
    question = KnowledgeQuestionInput(
        schema="knowledge.tool.question.v1",
        question_id="question.recovery",
        problem="Which launch date applies?",
        evidence_ids=(event.message_id,),
        recommendation="Confirm the date before publication.",
    )
    first = (
        question.model_copy(update={"evidence_ids": ("missing.event",)}).model_dump_json(
            by_alias=True
        )
        if invalid == "missing_evidence"
        else invalid
    )
    runner = CurationRunner(
        CurationDependencies(
            provider=QuestionProvider(first, question.model_dump_json(by_alias=True), error),
            tool_host=ToolHost(repository),
            dispositions=RepositorySourceDisposition(repository),
        )
    )
    work = processor.build_curation_work(job)
    # When: the provider sees the rejection and corrects the evidence or payload.
    result = (
        runner.run_batch("batch.question", (work,))[0]
        if batch
        else runner.run(work.request, work.trusted_context)
    )
    # Then: only the corrected question is stored, and the runner waits for its answer.
    assert result.status is CurationRunStatus.AWAITING_ANSWER
    assert result.decision_count == 2
    assert result.observations[0].result.error_code == error
    assert result.observations[1].result.status is ToolResultStatus.PENDING
    stored = RepositoryToolState(repository).question(processor.actor, question.question_id)
    assert stored is not None
    assert stored.evidence_ids == (event.message_id,)


@pytest.mark.parametrize("scenario", ["replayed", "denied", "budget"])
def test_question_recovery_preserves_terminal_boundaries(
    curation_input: CurationInput, scenario: str
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    context = work.trusted_context
    host = ToolHost(repository)
    question = KnowledgeQuestionInput(
        schema="knowledge.tool.question.v1",
        question_id="question.boundary",
        problem="Which launch date applies?",
        evidence_ids=(event.message_id,),
        recommendation="Confirm the date.",
    )
    if scenario == "replayed":
        saved = host.execute(
            KnowledgeToolName.KNOWLEDGE_QUESTION.value,
            question.model_dump(mode="json", by_alias=True),
            context,
        )
        assert saved.status is ToolResultStatus.PENDING
    if scenario == "denied":
        context = context.model_copy(
            update={"actor": context.actor.model_copy(update={"grants": ()})}
        )
    if scenario == "budget":
        question = question.model_copy(update={"evidence_ids": ("missing.event",)})
    encoded = question.model_dump_json(by_alias=True)
    runner = CurationRunner(
        CurationDependencies(
            provider=QuestionProvider(encoded, encoded, "question_evidence_not_found"),
            tool_host=host,
            dispositions=RepositorySourceDisposition(repository),
        ),
        limits=CurationLimits(max_decisions=2),
    )
    # When
    result = runner.run(work.request, context)
    # Then
    if scenario == "replayed":
        assert result.status is CurationRunStatus.AWAITING_ANSWER
        assert result.decision_count == 1
        assert result.observations[0].result.status is ToolResultStatus.REPLAYED
    if scenario == "denied":
        assert result.status is CurationRunStatus.FAILED
        assert result.decision_count == 1
        assert (
            RepositoryToolState(repository).question(processor.actor, question.question_id) is None
        )
    if scenario == "budget":
        assert result.status is CurationRunStatus.BUDGET_EXHAUSTED
        assert result.decision_count == 2
        assert result.error_code == "curation_decision_budget_exhausted"
        assert (
            RepositoryToolState(repository).question(processor.actor, question.question_id) is None
        )
