"""Composition root that routes broker tasks to their leaf tool executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ads_booster.marketing.hosted_candidate_judgment import (
    HostedCandidateJudgmentExecutor,
    PreparedCandidateJudgment,
)
from ads_booster.marketing.hosted_creative_judgment import (
    HostedCreativeJudgmentExecutor,
    PreparedCreativeJudgment,
)
from ads_booster.marketing.hosted_experiment_evaluation import (
    HostedExperimentEvaluationExecutor,
    PreparedExperimentEvaluation,
)
from ads_booster.marketing.hosted_generation import (
    HostedWorkspaceGenerationExecutor,
    PreparedHostedGeneration,
)
from ads_booster.marketing.hosted_judgment import (
    HostedMarketingJudgmentExecutor,
    PreparedMarketingJudgment,
)
from ads_booster.marketing.hosted_learning_judgment import (
    HostedLearningJudgmentExecutor,
    PreparedLearningJudgment,
)
from ads_booster.marketing.hosted_reassessment_judgment import (
    HostedOutcomeReassessmentExecutor,
    PreparedOutcomeReassessment,
)
from ads_booster.marketing.hosted_reference_research import (
    HostedReferenceResearchExecutor,
    PreparedReferenceResearch,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult
from ads_booster.marketing.native_capture import (
    HostedWorkspaceCaptureExecutor,
    PreparedCodexAppiumJob,
)
from ads_booster.marketing.worker_capabilities import MARKETING_JUDGMENT_CAPABILITIES

type MarketingJudgmentPrepared = (
    PreparedMarketingJudgment
    | PreparedCreativeJudgment
    | PreparedCandidateJudgment
    | PreparedExperimentEvaluation
    | PreparedLearningJudgment
    | PreparedReferenceResearch
    | PreparedOutcomeReassessment
)
type PlanlessPrepared = (
    PreparedCodexAppiumJob | PreparedHostedGeneration | MarketingJudgmentPrepared
)

_ROUTED_JUDGMENTS: Final = frozenset(
    {
        "shadow_strategy",
        "market_research",
        "creative_plan",
        "candidate_materialization",
        "experiment_evaluation",
        "learning_synthesis",
        "outcome_reassessment",
    }
)
if MARKETING_JUDGMENT_CAPABILITIES.keys() != _ROUTED_JUDGMENTS:
    raise RuntimeError("marketing judgment capability registry does not match the task router")


@dataclass(frozen=True, slots=True)
class PlanlessHostedTaskExecutor:
    """Top-level broker router; leaf executors continue to own every tool implementation."""

    capture: HostedWorkspaceCaptureExecutor
    generation: HostedWorkspaceGenerationExecutor
    judgment: HostedMarketingJudgmentExecutor
    creative_judgment: HostedCreativeJudgmentExecutor
    candidate_judgment: HostedCandidateJudgmentExecutor
    experiment_evaluation: HostedExperimentEvaluationExecutor
    learning_judgment: HostedLearningJudgmentExecutor
    reference_research: HostedReferenceResearchExecutor
    outcome_reassessment: HostedOutcomeReassessmentExecutor

    def prepare(self, task: MarketingTask) -> PlanlessPrepared:
        match task.kind:
            case TaskKind.CAPTURE:
                return self.capture.prepare(task)
            case TaskKind.GENERATE_CANDIDATES:
                return self.generation.prepare(task)
            case TaskKind.MARKETING_JUDGMENT:
                return self._prepare_marketing_judgment(task)
            case _:
                raise MarketingExecutionError("unsupported_hosted_task")

    def execute(self, prepared: PlanlessPrepared) -> TaskResult:
        if isinstance(prepared, PreparedHostedGeneration):
            return self.generation.execute(prepared)
        if isinstance(
            prepared,
            (
                PreparedMarketingJudgment,
                PreparedCreativeJudgment,
                PreparedCandidateJudgment,
                PreparedExperimentEvaluation,
                PreparedLearningJudgment,
                PreparedReferenceResearch,
                PreparedOutcomeReassessment,
            ),
        ):
            return self._execute_marketing_judgment(prepared)
        return self.capture.execute(prepared)

    def _prepare_marketing_judgment(self, task: MarketingTask) -> MarketingJudgmentPrepared:
        judgment = task.payload.get("judgment")
        if judgment == "shadow_strategy":
            prepared = self.judgment.prepare(task)
        elif judgment == "market_research":
            prepared = self.reference_research.prepare(task)
        elif judgment == "creative_plan":
            prepared = self.creative_judgment.prepare(task)
        elif judgment == "candidate_materialization":
            prepared = self.candidate_judgment.prepare(task)
        elif judgment == "experiment_evaluation":
            prepared = self.experiment_evaluation.prepare(task)
        elif judgment == "learning_synthesis":
            prepared = self.learning_judgment.prepare(task)
        elif judgment == "outcome_reassessment":
            prepared = self.outcome_reassessment.prepare(task)
        else:
            raise MarketingExecutionError("unsupported_marketing_judgment")
        return prepared

    def _execute_marketing_judgment(self, prepared: MarketingJudgmentPrepared) -> TaskResult:
        if isinstance(prepared, PreparedMarketingJudgment):
            result = self.judgment.execute(prepared)
        elif isinstance(prepared, PreparedCreativeJudgment):
            result = self.creative_judgment.execute(prepared)
        elif isinstance(prepared, PreparedCandidateJudgment):
            result = self.candidate_judgment.execute(prepared)
        elif isinstance(prepared, PreparedExperimentEvaluation):
            result = self.experiment_evaluation.execute(prepared)
        elif isinstance(prepared, PreparedReferenceResearch):
            result = self.reference_research.execute(prepared)
        elif isinstance(prepared, PreparedOutcomeReassessment):
            result = self.outcome_reassessment.execute(prepared)
        else:
            result = self.learning_judgment.execute(prepared)
        return result
