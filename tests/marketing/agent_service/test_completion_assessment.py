from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest

from ads_booster.agent.service.task_completion import CompletionContext, TaskCompletionService
from ads_booster.agent.service.task_progress import apply_proposal
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    AgentRunState,
    contract_sha256,
)
from ads_booster.contracts.task_progress import ExactResponseCheck, TaskProposal
from tests.marketing.agent_service.completion_fixtures import ExactTipAssessor, response_case
from tests.marketing.agent_service.test_task_progress import NOW, step

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.task_completion import (
        SemanticAssessmentRequest,
        SemanticAssessmentResult,
    )
    from ads_booster.contracts.task_progress import TaskObligation


def test_exact_response_is_assessed_without_tool_evidence(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    assessor = ExactTipAssessor()
    service = TaskCompletionService(case.repository, assessor)

    result = service.assess(case.task, case.candidate, CompletionContext(case.run, case.checkpoint))

    assert result.disposition == "satisfied"
    assert result.candidate_sha256 == contract_sha256(case.candidate)
    assert result.task_spec_sha256 == contract_sha256(case.task)
    assert assessor.requests[0].candidate == case.candidate


def test_host_exact_response_check_skips_semantic_assessor(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    obligation = case.task.obligations[0].model_copy(
        update={"verification": ExactResponseCheck(expected=case.candidate.answer)}
    )
    task = case.task.model_copy(update={"obligations": (obligation,)})
    checkpoint = case.checkpoint.model_copy(update={"spec_sha256": contract_sha256(task)})

    result = TaskCompletionService(case.repository, None).assess(
        task, case.candidate, CompletionContext(case.run, checkpoint)
    )

    assert result.disposition == "satisfied"
    assert result.obligations[0].mechanism == "host_exact_response"


def test_host_exact_response_mismatch_continues_without_semantic_assessor(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    obligation = case.task.obligations[0].model_copy(
        update={"verification": ExactResponseCheck(expected="Different exact answer")}
    )
    task = case.task.model_copy(update={"obligations": (obligation,)})
    checkpoint = case.checkpoint.model_copy(update={"spec_sha256": contract_sha256(task)})

    result = TaskCompletionService(case.repository, None).assess(
        task, case.candidate, CompletionContext(case.run, checkpoint)
    )

    assert result.disposition == "continue"
    assert result.obligations[0].status == "unsatisfied"
    assert result.uncovered_requirements == (obligation.description,)


def test_actor_cannot_add_self_selected_deterministic_verification(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    actor_obligation: TaskObligation = case.task.obligations[0].model_copy(
        update={
            "obligation_id": "actor-easy-check",
            "verification": ExactResponseCheck(expected=case.candidate.answer),
        }
    )
    proposal = TaskProposal(
        task_revision=case.task.task_revision,
        source_event_id=case.task.source_event_id,
        obligations=(actor_obligation,),
    )

    with pytest.raises(ValueError, match="actor proposal cannot select deterministic verification"):
        _ = apply_proposal(case.task, proposal)


def test_absent_assessor_blocks_response_completion(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    service = TaskCompletionService(case.repository, None)

    result = service.assess(case.task, case.candidate, CompletionContext(case.run, case.checkpoint))

    assert result.disposition == "blocked"
    assert result.reason == "verification_unavailable"
    assert result.assessment_cache_sha256 is None
    assert result.semantic_result is None


def test_exact_semantic_assessment_is_reused_from_canonical_record(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    first_assessor = ExactTipAssessor()
    first = TaskCompletionService(case.repository, first_assessor).assess(
        case.task, case.candidate, CompletionContext(case.run, case.checkpoint)
    )
    record = AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id="cached-assessment",
        run_id=case.run.run_id,
        kind=AgentRecordKind.EVIDENCE,
        payload_schema_version=first.schema_version,
        payload=first.model_dump(mode="json"),
        payload_sha256=contract_sha256(first),
        occurred_at=NOW,
    )
    current = case.repository.append_step(
        case.run,
        step().model_copy(update={"run_id": case.run.run_id}),
        state=AgentRunState.RUNNING,
        expected_revision=case.run.revision,
        records=(record,),
    )
    second_assessor = ExactTipAssessor()
    checkpoint = case.checkpoint.model_copy(update={"decision_calls": 2, "assessment_calls": 2})

    second = TaskCompletionService(case.repository, second_assessor).assess(
        case.task, case.candidate, CompletionContext(current, checkpoint)
    )

    assert second.disposition == "satisfied"
    assert second.cache_hit is True
    assert first_assessor.requests != []
    assert second_assessor.requests == []


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_omitted_original_deliverable_never_passes_on_response_proposal(
    tmp_path: Path, attempt: int
) -> None:
    case = response_case(tmp_path / "state.db")
    task = case.task.model_copy(update={"original_objective": "Create a launch image"})
    checkpoint = case.checkpoint.model_copy(
        update={
            "spec_sha256": contract_sha256(task),
            "assessment_calls": attempt,
            "decision_calls": attempt,
        }
    )
    service = TaskCompletionService(case.repository, ExactTipAssessor())

    result = service.assess(task, case.candidate, CompletionContext(case.run, checkpoint))

    assert result.disposition == ("blocked" if attempt == 3 else "continue")
    assert result.reason == "completion_unsatisfied"


def test_candidate_evidence_hash_must_resolve_to_canonical_record(tmp_path: Path) -> None:
    case = response_case(tmp_path / "state.db")
    candidate = case.candidate.model_copy(update={"evidence_sha256s": ("f" * 64,)})
    checkpoint = case.checkpoint.model_copy(update={"candidate": candidate})
    assessor = ExactTipAssessor()

    result = TaskCompletionService(case.repository, assessor).assess(
        case.task, candidate, CompletionContext(case.run, checkpoint)
    )

    assert result.disposition == "blocked"
    assert result.reason == "completion_evidence_invalid"
    assert assessor.requests == []


class InvalidAssessor:
    def __init__(self, failure: Literal["request", "candidate", "duplicate", "exception"]) -> None:
        self.failure: Literal["request", "candidate", "duplicate", "exception"] = failure

    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        result = ExactTipAssessor().assess(request)
        match self.failure:
            case "request":
                return result.model_copy(update={"request_sha256": "a" * 64})
            case "candidate":
                return result.model_copy(update={"candidate_sha256": "a" * 64})
            case "duplicate":
                return result.model_copy(update={"obligations": result.obligations * 2})
            case "exception":
                message = "Private provider details"
                raise RuntimeError(message)
            case _:
                assert_never(self.failure)


@pytest.mark.parametrize("failure", ["request", "candidate", "duplicate", "exception"])
def test_invalid_assessor_cannot_release_completion(
    tmp_path: Path, failure: Literal["request", "candidate", "duplicate", "exception"]
) -> None:
    case = response_case(tmp_path / "state.db")

    result = TaskCompletionService(case.repository, InvalidAssessor(failure)).assess(
        case.task, case.candidate, CompletionContext(case.run, case.checkpoint)
    )

    assert result.disposition == "blocked"
    assert result.reason == "verification_unavailable"
    assert result.assessment_cache_sha256 is None
    assert result.semantic_result is None


@pytest.mark.parametrize(
    "change", [{"result_links": ("https://example.com/changed",)}, {"answer": "Changed advice"}]
)
def test_candidate_mutation_invalidates_exact_assessment(
    tmp_path: Path, change: dict[str, str | tuple[str, ...]]
) -> None:
    case = response_case(tmp_path / "state.db")
    candidate = case.candidate.model_copy(update=change)

    result = TaskCompletionService(case.repository, ExactTipAssessor()).assess(
        case.task, candidate, CompletionContext(case.run, case.checkpoint)
    )

    assert result.disposition == "blocked"
    assert result.reason == "completion_binding_invalid"


@pytest.mark.parametrize("kind", ["artifact", "effect"])
def test_semantic_success_cannot_replace_mandatory_owner_proof(
    tmp_path: Path, kind: Literal["artifact", "effect"]
) -> None:
    case = response_case(tmp_path / "state.db")
    task = case.task.model_copy(
        update={
            "obligations": (case.task.obligations[0].model_copy(update={"kind": kind}),),
        }
    )
    checkpoint = case.checkpoint.model_copy(update={"spec_sha256": contract_sha256(task)})

    result = TaskCompletionService(case.repository, ExactTipAssessor()).assess(
        task, case.candidate, CompletionContext(case.run, checkpoint)
    )

    assert result.disposition == "continue"
    assert result.obligations[0].status == "unsatisfied"
    assert result.obligations[0].mechanism == "host_owner_proof"
