from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.marketing.hosted_task_router import PlanlessHostedTaskExecutor
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind
from ads_booster.marketing.worker_capabilities import MARKETING_JUDGMENT_CAPABILITIES

if TYPE_CHECKING:
    from ads_booster.marketing.hosted_candidate_judgment import HostedCandidateJudgmentExecutor
    from ads_booster.marketing.hosted_creative_judgment import HostedCreativeJudgmentExecutor
    from ads_booster.marketing.hosted_experiment_evaluation import (
        HostedExperimentEvaluationExecutor,
    )
    from ads_booster.marketing.hosted_generation import HostedWorkspaceGenerationExecutor
    from ads_booster.marketing.hosted_judgment import HostedMarketingJudgmentExecutor
    from ads_booster.marketing.hosted_learning_judgment import HostedLearningJudgmentExecutor
    from ads_booster.marketing.hosted_reassessment_judgment import (
        HostedOutcomeReassessmentExecutor,
    )
    from ads_booster.marketing.hosted_reference_research import HostedReferenceResearchExecutor
    from ads_booster.marketing.native_capture import HostedWorkspaceCaptureExecutor
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True)
class StubExecutor:
    name: str

    def prepare(self, _task: MarketingTask) -> object:
        return self


def task(kind: TaskKind, judgment: str | None = None) -> MarketingTask:
    payload: JsonObject = {}
    if judgment is not None:
        payload["judgment"] = judgment
    return MarketingTask(
        task_id=f"task-{kind.value}-{judgment or 'base'}",
        run_id="run-1",
        account_id="trace_kr",
        kind=kind,
        idempotency_key=f"task:{kind.value}:{judgment or 'base'}",
        payload=payload,
        created_at=datetime.now(UTC),
    )


def router() -> tuple[PlanlessHostedTaskExecutor, dict[str, StubExecutor]]:
    executors = {
        "capture": StubExecutor("capture"),
        "generation": StubExecutor("generation"),
        "shadow_strategy": StubExecutor("shadow_strategy"),
        "creative_plan": StubExecutor("creative_plan"),
        "candidate_materialization": StubExecutor("candidate_materialization"),
        "experiment_evaluation": StubExecutor("experiment_evaluation"),
        "learning_synthesis": StubExecutor("learning_synthesis"),
        "market_research": StubExecutor("market_research"),
        "outcome_reassessment": StubExecutor("outcome_reassessment"),
    }
    return (
        PlanlessHostedTaskExecutor(
            capture=cast("HostedWorkspaceCaptureExecutor", cast("object", executors["capture"])),
            generation=cast(
                "HostedWorkspaceGenerationExecutor", cast("object", executors["generation"])
            ),
            judgment=cast(
                "HostedMarketingJudgmentExecutor", cast("object", executors["shadow_strategy"])
            ),
            creative_judgment=cast(
                "HostedCreativeJudgmentExecutor", cast("object", executors["creative_plan"])
            ),
            candidate_judgment=cast(
                "HostedCandidateJudgmentExecutor",
                cast("object", executors["candidate_materialization"]),
            ),
            experiment_evaluation=cast(
                "HostedExperimentEvaluationExecutor",
                cast("object", executors["experiment_evaluation"]),
            ),
            learning_judgment=cast(
                "HostedLearningJudgmentExecutor", cast("object", executors["learning_synthesis"])
            ),
            reference_research=cast(
                "HostedReferenceResearchExecutor", cast("object", executors["market_research"])
            ),
            outcome_reassessment=cast(
                "HostedOutcomeReassessmentExecutor",
                cast("object", executors["outcome_reassessment"]),
            ),
        ),
        executors,
    )


def test_router_keeps_existing_tools_and_all_reasoning_subtypes_at_the_composition_root() -> None:
    task_router, executors = router()

    assert task_router.prepare(task(TaskKind.CAPTURE)) is executors["capture"]
    assert task_router.prepare(task(TaskKind.GENERATE_CANDIDATES)) is executors["generation"]
    for judgment in MARKETING_JUDGMENT_CAPABILITIES:
        prepared = task_router.prepare(task(TaskKind.MARKETING_JUDGMENT, judgment))
        assert prepared is executors[judgment]

    with pytest.raises(MarketingExecutionError, match="unsupported_marketing_judgment"):
        _ = task_router.prepare(task(TaskKind.MARKETING_JUDGMENT, "unknown"))


def test_python_and_control_plane_freeze_the_same_reasoning_capability_versions() -> None:
    source = (
        Path(__file__).parents[2] / "cloudflare" / "src" / "marketing-worker-capabilities.js"
    ).read_text()
    block = source.split("MARKETING_JUDGMENT_CAPABILITIES = Object.freeze({", maxsplit=1)[1]
    block = block.split("});", maxsplit=1)[0]
    control_plane = dict(re.findall(r'^\s+([a-z_]+): "([a-z0-9_]+)",$', block, re.MULTILINE))

    assert control_plane == MARKETING_JUDGMENT_CAPABILITIES
