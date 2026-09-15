from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.application import CreateAgentRunRequest, MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.contracts.task_completion import ObligationAssessment, SemanticAssessmentResult
from ads_booster.tools.completion_proofs import CanonicalCompletionProofs, CompletionArtifactOwners
from ads_booster.tools.image_generation import descriptor, read_artifact
from tests.marketing.agent_service.completion_fixtures import NOW
from tests.marketing.agent_service.completion_image_fixtures import FixtureImageTool
from tests.marketing.agent_service.test_task_completion import (
    CompletionScript,
    drain_completion,
    stop_decision,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.task_completion import SemanticAssessmentRequest


class DistinctImagesAssessor:
    def __init__(self, count: int) -> None:
        self.count: int = count

    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        evidence = tuple(
            item.evidence_sha256
            for item in request.evidence
            if item.verified and item.kind == "artifact"
        )
        supported = (
            request.original_objective == f"Create {self.count} distinct PNG images"
            and len(set(request.candidate.attachment_refs)) == self.count
            and len(evidence) >= self.count
        )
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=supported,
            uncovered_requirements=() if supported else ("More distinct images required",),
            obligations=tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="satisfied" if supported else "unsatisfied",
                    mechanism="distinct_artifact_count",
                    reason="Count verified PNG artifacts",
                    evidence_sha256s=evidence,
                )
                for item in request.obligations
            ),
        )


@pytest.mark.parametrize("count", [2, 4])
def test_artifact_assessment_continues_incomplete_multi_image_work(
    tmp_path: Path, count: int
) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "state.db")
    images = tmp_path / "images"
    colors = ("blue", "green", "red", "yellow")[:count]
    adapter = FixtureImageTool(images, colors=colors)
    planner = CompletionScript(
        (
            *(
                ReasoningDecision(
                    schema_version="trace.reasoning-decision.v1",
                    action="invoke_tool",
                    capability_id="creative.image.generate",
                    tool_input={"prompt": color},
                    expected_outcome="Create next image",
                    reasoning_summary="Generate next variant",
                )
                for color in colors
            ),
            stop_decision("Images ready"),
        )
    )
    service = MarketingAgentService(
        repository=repository,
        registry=ToolRegistry((descriptor(now=NOW),)),
        reasoning=planner,
        tools={"creative.image.generate": adapter},
        runtime_store=SqliteSessionStore(repository.database_path),
        completion=TaskCompletionService(
            repository,
            DistinctImagesAssessor(count),
            CanonicalCompletionProofs(repository, CompletionArtifactOwners(image_root=images)),
        ),
        clock=lambda: NOW,
    )
    run = service.create(
        CreateAgentRunRequest(
            run_id="multi",
            tenant_id="trace",
            goal=AgentGoal(
                objective=f"Create {count} distinct PNG images",
                success_criteria=(f"Deliver {count} distinct PNG images",),
            ),
            budget=AgentBudget(max_tool_calls=6, max_cost_units=10),
        ),
        now=NOW,
    )
    for index in range(count):
        run = drain_completion(service, run)
        assert run.state is AgentRunState.AWAITING_APPROVAL
        assert adapter.calls == index
        run = service.decide_approval(
            "trace",
            run.run_id,
            approver_id="member",
            granted=True,
            now=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
    run = drain_completion(service, run)
    assert run.state is AgentRunState.COMPLETED
    assert adapter.calls == count
    task = project_task(run, repository.records("trace", run.run_id))
    assert task.checkpoint.assessment_calls <= 3
    assert task.checkpoint.candidate is not None
    assert len(task.checkpoint.candidate.attachment_refs) == count
    assert (
        len({read_artifact(images, digest) for digest in task.checkpoint.candidate.attachment_refs})
        == count
    )
