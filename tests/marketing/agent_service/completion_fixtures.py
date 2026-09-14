from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRun, contract_sha256
from ads_booster.contracts.task_completion import (
    CompletionCandidate,
    ObligationAssessment,
    SemanticAssessmentRequest,
    SemanticAssessmentResult,
)
from ads_booster.contracts.task_progress import TaskCheckpoint, TaskObligation, TaskSpec

NOW = datetime(2026, 9, 3, tzinfo=UTC)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompletionCase:
    repository: SqliteAgentRunRepository
    run: AgentRun
    task: TaskSpec
    checkpoint: TaskCheckpoint
    candidate: CompletionCandidate


def response_case(database: Path) -> CompletionCase:
    goal = AgentGoal(objective="Give one launch tip", success_criteria=("One sentence of advice",))
    repository = SqliteAgentRunRepository(database)
    run = repository.create(
        AgentRun(
            schema_version="trace.agent-run.v1",
            run_id="completion-run",
            tenant_id="trace",
            goal=goal,
            budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    task = TaskSpec(
        task_id="completion-task",
        task_revision=1,
        source_event_id="request-one",
        source_event_ids=("request-one",),
        objective=goal.objective,
        original_objective=goal.objective,
        original_criteria=goal.success_criteria,
        obligations=(
            TaskObligation(
                obligation_id="tip",
                kind="response",
                description=goal.success_criteria[0],
                source_refs=("request-one",),
            ),
        ),
    )
    answer = "Start with a small launch."
    candidate = CompletionCandidate(
        candidate_id="candidate-one",
        task_id=task.task_id,
        task_revision=1,
        answer=answer,
        answer_sha256=contract_sha256({"answer": answer}),
    )
    checkpoint = TaskCheckpoint(
        task_id=task.task_id,
        task_revision=1,
        spec_sha256=contract_sha256(task),
        segment_id="segment-one",
        unresolved_obligation_ids=("tip",),
        decision_calls=1,
        assessment_calls=1,
        candidate=candidate,
    )
    return CompletionCase(repository, run, task, checkpoint, candidate)


class ExactTipAssessor:
    assessment_identity: str = "fixture-exact-tip-v1"

    def __init__(self) -> None:
        self.requests: list[SemanticAssessmentRequest] = []

    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        self.requests.append(request)
        supported = (
            request.original_objective == "Give one launch tip"
            and request.original_criteria == ("One sentence of advice",)
            and request.candidate.answer == "Start with a small launch."
        )
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=supported,
            uncovered_requirements=() if supported else (request.original_objective,),
            obligations=tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="satisfied" if supported else "unsatisfied",
                    mechanism="fixture_exact_tip",
                    reason="Exact fixture advice" if supported else "Different requested result",
                )
                for item in request.obligations
            ),
        )


class ScriptedAssessor:
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=True,
            obligations=tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="satisfied",
                    mechanism="scripted_orchestration_fixture",
                    reason="Scripted acceptance",
                )
                for item in request.obligations
            ),
        )


@final
class FixtureMarketingAgentService(MarketingAgentService):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.completion = TaskCompletionService(self.repository, ScriptedAssessor())
        self.clock = lambda: NOW
        self.monotonic_clock = lambda: 0.0
