from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, assert_never

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.application import CreateAgentRunRequest, MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.tools.completion_proofs import (
    CanonicalCompletionProofs,
    CompletionArtifactOwners,
)
from ads_booster.tools.image_generation import descriptor, read_artifact
from tests.marketing.agent_service.completion_fixtures import NOW
from tests.marketing.agent_service.completion_image_fixtures import (
    FixtureImageTool,
    ImageExistenceAssessor,
    RepairingImagePlanner,
)
from tests.marketing.agent_service.test_task_completion import CompletionScript, drain_completion

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.task_completion import (
        SemanticAssessmentRequest,
        SemanticAssessmentResult,
    )


@pytest.mark.parametrize("tamper", [False, True])
def test_repeated_completed_effect_reuses_verified_result(tmp_path: Path, tamper: bool) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "state.db")
    images = tmp_path / "images"
    adapter = FixtureImageTool(images)
    planner = CompletionScript(
        (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.image.generate",
                tool_input={"prompt": "Blue square"},
                expected_outcome="Create image",
                reasoning_summary="Generate",
            ),
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
            ImageExistenceAssessor(),
            CanonicalCompletionProofs(repository, CompletionArtifactOwners(image_root=images)),
        ),
        clock=lambda: NOW,
    )

    def invalidate(point: str) -> None:
        if tamper and point == "verify_committed":
            _ = next(images.glob("*.png")).write_bytes(b"changed")

    service.fault_hook = invalidate
    run = service.create(
        CreateAgentRunRequest(
            run_id="repeat",
            tenant_id="trace",
            goal=AgentGoal(objective="Create image", success_criteria=("Readable PNG",)),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
        ),
        now=NOW,
    )
    run = drain_completion(service, run)
    assert run.state is AgentRunState.AWAITING_APPROVAL
    run = service.decide_approval(
        "trace",
        run.run_id,
        approver_id="member",
        granted=True,
        now=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    run = drain_completion(service, run)
    assert adapter.calls == 1
    if tamper:
        assert run.state is AgentRunState.AWAITING_APPROVAL
        return
    assert run.state is AgentRunState.BLOCKED
    assert run.blocked_reason == "no_progress"


def test_successful_artifact_is_assessed_before_another_generation(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "state.db")
    images = tmp_path / "images"
    adapter = FixtureImageTool(images)
    planner = CompletionScript(
        tuple(
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.image.generate",
                tool_input={"prompt": prompt},
                expected_outcome="Readable PNG image",
                reasoning_summary="Generate",
            )
            for prompt in ("Blue square", "Another blue square")
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
            ImageExistenceAssessor(),
            CanonicalCompletionProofs(repository, CompletionArtifactOwners(image_root=images)),
        ),
        clock=lambda: NOW,
    )
    run = service.create(
        CreateAgentRunRequest(
            run_id="artifact-first",
            tenant_id="trace",
            goal=AgentGoal(
                objective="Create a blue PNG image", success_criteria=("Readable PNG image",)
            ),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
        ),
        now=NOW,
    )
    run = drain_completion(service, run)
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
    assert adapter.calls == 1
    assert len(planner.requests) == 1
    task = project_task(run, repository.records("trace", run.run_id))
    assert task.checkpoint.candidate is not None
    assert task.checkpoint.candidate.attachment_refs


class InvalidatingAssessor:
    def __init__(self, root: Path) -> None:
        self.root: Path = root

    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        result = ImageExistenceAssessor().assess(request)
        if result.requested_deliverables_supported:
            next(self.root.glob("*.png")).unlink()
        return result


@pytest.mark.parametrize(
    "failure",
    ["none", "no_effect", "missing", "tamper", "symlink", "during_assessment", "long_brief"],
)
def test_premature_stop_repairs_with_approved_actual_artifact(
    tmp_path: Path,
    failure: Literal[
        "none", "no_effect", "missing", "tamper", "symlink", "during_assessment", "long_brief"
    ],
) -> None:
    database = tmp_path / "state.db"
    repository = SqliteAgentRunRepository(database)
    images = tmp_path / "images"
    adapter = FixtureImageTool(images, "no_effect" if failure == "no_effect" else "succeeded")
    planner = RepairingImagePlanner(
        "Blue square. " * 400 if failure == "long_brief" else "Blue square"
    )
    service = MarketingAgentService(
        repository=repository,
        registry=ToolRegistry((descriptor(now=NOW),)),
        reasoning=planner,
        tools={"creative.image.generate": adapter},
        runtime_store=SqliteSessionStore(database),
        completion=TaskCompletionService(
            repository,
            InvalidatingAssessor(images)
            if failure == "during_assessment"
            else ImageExistenceAssessor(),
            CanonicalCompletionProofs(repository, CompletionArtifactOwners(image_root=images)),
        ),
        clock=lambda: NOW,
    )
    changed = False

    def invalidate(point: str) -> None:
        nonlocal changed
        if point != "assessment_reserved" or adapter.calls == 0 or changed:
            return
        changed = True
        paths = tuple(images.glob("*.png"))
        match failure:
            case "missing":
                paths[0].unlink()
            case "tamper":
                _ = paths[0].write_bytes(b"not the accepted PNG")
            case "symlink":
                target = tmp_path / "outside.png"
                _ = paths[0].rename(target)
                paths[0].symlink_to(target)
            case "none" | "no_effect" | "during_assessment" | "long_brief":
                return
            case _:
                assert_never(failure)

    service.fault_hook = invalidate
    run = service.create(
        CreateAgentRunRequest(
            run_id="image-repair",
            tenant_id="trace",
            goal=AgentGoal(
                objective="Create a blue PNG image", success_criteria=("Readable PNG image",)
            ),
            budget=AgentBudget(max_tool_calls=2, max_cost_units=10),
        ),
        now=NOW,
    )
    run = drain_completion(service, run)
    assert run.state is AgentRunState.AWAITING_APPROVAL
    assert adapter.calls == 0
    records = repository.records(run.tenant_id, run.run_id)
    pending = next(
        item
        for item in reversed(records)
        if item.payload_schema_version == "trace.tool-invocation.v1"
    )
    run = service.decide_approval(
        run.tenant_id,
        run.run_id,
        approver_id="member",
        granted=True,
        expected_invocation_sha256=contract_sha256(pending.payload),
        now=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    run = drain_completion(service, run)

    assert adapter.calls == 1
    task = project_task(run, repository.records(run.tenant_id, run.run_id))
    if failure not in {"none", "long_brief"}:
        assert run.state is AgentRunState.BLOCKED
        assert task.checkpoint.disposition == "blocked"
        assert task.checkpoint.wait_reason in {
            "completion_unverifiable",
            "completion_evidence_invalid",
        }
        return
    assert run.state is AgentRunState.COMPLETED
    assert task.checkpoint.disposition == "satisfied"
    assert task.checkpoint.assessment_calls == 2
    assert task.checkpoint.candidate is not None
    assert task.checkpoint.candidate.answer == "The image artifact is ready."
    paths = tuple(images.glob("*.png"))
    assert len(paths) == 1
    assert read_artifact(images, paths[0].stem)
    assert any(item.completion_feedback is not None for item in planner.requests)
