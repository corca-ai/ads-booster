from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.application import CreateAgentRunRequest, MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.task_progress import project_task
from ads_booster.bootstrap.integrations import AgentServiceIntegrationConfig, ConfiguredAgentTools
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    AgentRunState,
    contract_sha256,
)
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.contracts.task_completion import (
    ObligationAssessment,
    SemanticAssessmentRequest,
    SemanticAssessmentResult,
)
from ads_booster.tools.completion_proofs import CanonicalCompletionProofs
from tests.marketing.agent_service.test_application import NOW
from tests.marketing.agent_service.test_creative_procedures import NeverResearch

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import AgentRun


class BaselineAssessor:
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        supported = (
            request.original_objective == "Give one launch tip"
            and request.original_criteria == ("One sentence of advice",)
            and request.candidate.answer == "A small launch makes it easier to learn."
        )
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=supported,
            uncovered_requirements=() if supported else request.original_criteria,
            obligations=tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="satisfied" if supported else "unsatisfied",
                    mechanism="fixture_baseline_deliverable",
                    reason="Exact tip" if supported else "Required deliverable absent",
                )
                for item in request.obligations
            ),
        )


def drain_completion(service: MarketingAgentService, run: AgentRun) -> AgentRun:
    for _ in range(8):
        if run.state is not AgentRunState.RUNNING:
            return run
        run = service.drive(run.tenant_id, run.run_id, now=NOW)
    pytest.fail("Completion did not reach a real boundary within eight slices")


class CompletionScript:
    def __init__(self, decisions: tuple[ReasoningDecision, ...]) -> None:
        self.decisions: tuple[ReasoningDecision, ...] = decisions
        self.requests: list[ReasoningRequest] = []

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = self.decisions[min(len(self.requests), len(self.decisions) - 1)]
        self.requests.append(request)
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fixture.completion-script",
                model_id="scripted",
                request_sha256=contract_sha256(request),
                output_schema_sha256="d" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def stop_decision(answer: str) -> ReasoningDecision:
    return ReasoningDecision(
        schema_version="trace.reasoning-decision.v1",
        action="stop",
        expected_outcome="Return the requested deliverable",
        reasoning_summary=answer,
    )


def completion_service(database: Path, provider: CompletionScript) -> MarketingAgentService:
    configured = ConfiguredAgentTools(
        config=AgentServiceIntegrationConfig(), research_runner=NeverResearch()
    )
    repository = SqliteAgentRunRepository(database)
    return MarketingAgentService(
        repository=repository,
        registry=ToolRegistry(configured.descriptors(now=NOW)),
        reasoning=provider,
        tools=configured.adapters(),
        runtime_store=SqliteSessionStore(database),
        completion=TaskCompletionService(
            repository, BaselineAssessor(), CanonicalCompletionProofs(repository)
        ),
        clock=lambda: NOW,
    )


def completion_request(goal: AgentGoal) -> CreateAgentRunRequest:
    return CreateAgentRunRequest(
        run_id="completion-baseline",
        tenant_id="trace",
        goal=goal,
        budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
    )


def test_stop_requires_the_requested_artifact(tmp_path: Path) -> None:
    # Given a required output file and a provider that only claims it finished.
    artifact = tmp_path / "launch.png"
    provider = CompletionScript((stop_decision("The launch image is ready."),))
    service = completion_service(tmp_path / "service.db", provider)
    request = completion_request(
        AgentGoal(
            objective=f"Create a launch image at {artifact}",
            success_criteria=(f"A readable PNG artifact exists at {artifact}",),
        )
    )

    # When the actual service handles the stop.
    run = drain_completion(service, service.create(request, now=NOW))

    # Then absent bytes cannot yield a completed Run.
    assert not artifact.exists()
    assert service.repository.get(run.tenant_id, run.run_id) == run
    assert run.state is AgentRunState.BLOCKED
    task = project_task(run, service.repository.records(run.tenant_id, run.run_id))
    assert task.checkpoint.assessment_calls == 3
    assert task.checkpoint.wait_reason == "completion_unsatisfied"


@pytest.mark.parametrize("deliverable", ["image artifact", "published campaign"])
def test_preparation_receipt_cannot_complete_creation_or_effect(
    tmp_path: Path, deliverable: str
) -> None:
    # Given an actual preparation adapter and a request for its downstream result.
    provider = CompletionScript(
        (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.prepare",
                tool_input={"task": "mood", "inputs": {"request": "Autumn campus"}},
                expected_outcome="Prepare a production brief",
                reasoning_summary="Prepare the creative work",
            ),
            stop_decision("The creative work is complete."),
        )
    )
    service = completion_service(tmp_path / "service.db", provider)
    request = completion_request(
        AgentGoal(
            objective=f"Produce the {deliverable}",
            success_criteria=(f"The {deliverable} exists with verified owner evidence",),
        )
    )

    # When a genuine prepare receipt is followed by stop.
    run = drain_completion(service, service.create(request, now=NOW))

    # Then preparation remains distinct from the requested creation or effect.
    records = service.repository.records(run.tenant_id, run.run_id)
    outputs = [
        record.payload["output"]
        for record in records
        if record.payload_schema_version == "trace.tool-output-evidence.v1"
    ]
    assert outputs
    assert all(isinstance(output, dict) for output in outputs)
    assert any(
        isinstance(output, dict) and output.get("status") == "prepared_not_executed"
        for output in outputs
    )
    assert run.state is AgentRunState.BLOCKED
    task = project_task(run, records)
    assert task.checkpoint.assessment_calls == 3
    assert task.checkpoint.wait_reason == "completion_unsatisfied"


def test_response_only_answer_completes_without_tool_receipts(tmp_path: Path) -> None:
    # Given a response-only request with its exact requested answer.
    answer = "A small launch makes it easier to learn."
    service = completion_service(
        tmp_path / "service.db", CompletionScript((stop_decision(answer),))
    )
    request = completion_request(
        AgentGoal(objective="Give one launch tip", success_criteria=("One sentence of advice",))
    )

    # When the service receives the answer.
    run = drain_completion(service, service.create(request, now=NOW))

    # Then completion needs no fabricated tool execution.
    records = service.repository.records(run.tenant_id, run.run_id)
    assert run.state is AgentRunState.COMPLETED
    assert not any(record.kind is AgentRecordKind.RECEIPT for record in records)
    assert any(
        record.payload.get("decision") == stop_decision(answer).model_dump(mode="json")
        for record in records
    )
