from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.task_completion import CompletionContext, TaskCompletionService
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_completion import (
    ObligationAssessment,
    SemanticAssessmentRequest,
    SemanticAssessmentResult,
)
from ads_booster.contracts.task_instruction import TaskInstruction
from ads_booster.contracts.task_progress import ExactResponseCheck
from tests.marketing.agent_service.completion_fixtures import (
    CompletionCase,
    ExactTipAssessor,
    response_case,
)

if TYPE_CHECKING:
    from pathlib import Path


def corrected_case(database: Path) -> CompletionCase:
    case = response_case(database)
    task = case.task.model_copy(
        update={
            "task_revision": 3,
            "prior_revision": 2,
            "source_event_id": "draft-only",
            "source_event_ids": ("request-one", "japanese", "draft-only"),
            "objective": "Make it shorter; only provide the draft text",
            "original_objective": "Create an English image",
            "original_criteria": ("Readable image",),
            "admitted_instructions": (
                TaskInstruction(source_event_id="request-one", text="Create an English image"),
                TaskInstruction(source_event_id="japanese", text="Use Japanese instead"),
                TaskInstruction(
                    source_event_id="draft-only",
                    text="Make it shorter; only provide the draft text",
                ),
            ),
            "obligations": (
                case.task.obligations[0].model_copy(
                    update={"kind": "artifact", "description": "Readable image"}
                ),
            ),
        }
    )
    candidate = case.candidate.model_copy(update={"task_revision": 3})
    checkpoint = case.checkpoint.model_copy(
        update={
            "task_revision": 3,
            "spec_sha256": contract_sha256(task),
            "candidate": candidate,
        }
    )
    return replace(case, task=task, candidate=candidate, checkpoint=checkpoint)


class SupersedingAssessor:
    def __init__(self, source: str) -> None:
        self.source: str = source
        self.requests: list[SemanticAssessmentRequest] = []

    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        self.requests.append(request)
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=True,
            obligations=tuple(
                ObligationAssessment.model_validate(
                    {
                        "obligation_id": item.obligation_id,
                        "status": "superseded",
                        "superseded_by_event_id": self.source,
                        "mechanism": "fixture_supersession",
                        "reason": "User explicitly requested draft text only",
                    }
                )
                for item in request.obligations
            ),
        )


@pytest.mark.parametrize("source", ["draft-only", "request-one", "forged"])
def test_only_later_admitted_instruction_can_supersede_artifact(
    tmp_path: Path, source: str
) -> None:
    case = corrected_case(tmp_path / "state.db")
    assessor = SupersedingAssessor(source)

    result = TaskCompletionService(case.repository, assessor).assess(
        case.task, case.candidate, CompletionContext(case.run, case.checkpoint)
    )

    assert result.disposition == ("satisfied" if source == "draft-only" else "blocked")
    assert assessor.requests[0].admitted_instructions == case.task.admitted_instructions


def test_later_correction_can_supersede_failed_host_exact_check(tmp_path: Path) -> None:
    case = corrected_case(tmp_path / "state.db")
    obligation = case.task.obligations[0].model_copy(
        update={
            "kind": "response",
            "verification": ExactResponseCheck(expected="Old exact answer"),
        }
    )
    task = case.task.model_copy(update={"obligations": (obligation,)})
    checkpoint = case.checkpoint.model_copy(update={"spec_sha256": contract_sha256(task)})
    assessor = SupersedingAssessor("draft-only")

    result = TaskCompletionService(case.repository, assessor).assess(
        task, case.candidate, CompletionContext(case.run, checkpoint)
    )

    assert result.disposition == "satisfied"
    assert result.obligations[0].status == "superseded"
    assert assessor.requests[0].obligations[0].obligation_id == obligation.obligation_id


class InferredArtifactAssessor:
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        return (
            ExactTipAssessor()
            .assess(request)
            .model_copy(update={"required_evidence_kinds": ("artifact",)})
        )


def test_optimistic_assessor_still_needs_inferred_artifact_proof(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")

    result = TaskCompletionService(case.repository, InferredArtifactAssessor()).assess(
        case.task, case.candidate, CompletionContext(case.run, case.checkpoint)
    )

    assert result.disposition == "continue"
    assert result.reason == "completion_unsatisfied"
    assert result.uncovered_requirements == (
        "Missing required artifact owner proof from admitted instructions",
    )
