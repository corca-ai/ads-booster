from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import CapabilitySnapshot, contract_sha256
from ads_booster.contracts.reasoning import ReasoningRequestV2
from ads_booster.providers.codex_reasoning import CodexReasoningError, CodexReasoningProvider
from tests.marketing.agent_service.completion_fixtures import response_case
from tests.marketing.agent_service.test_application import NOW
from tests.providers.test_codex_completion import assert_strict_schema

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class V2Runner:
    def __init__(self, output: JsonObject) -> None:
        self.output: JsonObject = output
        self.prompt: str = ""

    def run_marketing_judgment_job(
        self, prompt: str, schema: JsonObject, *, workspace: Path, timeout_seconds: float
    ) -> JsonObject:
        self.prompt = prompt
        _ = workspace, timeout_seconds
        assert_strict_schema(schema)
        return self.output


def v2_request(database: Path) -> ReasoningRequestV2:
    case = response_case(database)
    return ReasoningRequestV2(
        run_id=case.run.run_id,
        phase="plan",
        goal=case.run.goal,
        capability_snapshot=CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id="snapshot-one",
            run_id=case.run.run_id,
            descriptors=(),
            created_at=NOW,
        ),
        remaining_tool_calls=4,
        remaining_cost_units=10,
        task=case.task,
        checkpoint=case.checkpoint,
    )


def wire_stop(request: ReasoningRequestV2) -> JsonObject:
    candidate = request.checkpoint.candidate
    assert candidate is not None
    return {
        "schema_version": "trace.reasoning-decision.v2",
        "action": "stop",
        "capability_id": None,
        "tool_input_json": None,
        "expected_outcome": "Advice delivered",
        "reasoning_summary": "Internal selection rationale",
        "proposed_action_kind": None,
        "proposed_brand_ref": None,
        "authorization_message": None,
        "pending_approval_action": "preserve",
        "task_proposal": None,
        "completion_candidate": candidate.model_dump(mode="json", exclude={"answer_sha256"}),
    }


def test_v2_candidate_is_exact_and_host_hashes_answer(tmp_path: Path) -> None:
    request = v2_request(tmp_path / "state.db")

    result = CodexReasoningProvider(V2Runner(wire_stop(request)), tmp_path, "fixture").plan_v2(
        request
    )

    assert result.decision.completion_candidate == request.checkpoint.candidate
    assert result.receipt.request_sha256 == contract_sha256(request)
    assert result.receipt.decision_sha256 == contract_sha256(result.decision)


def test_v2_request_and_decision_preserve_conversational_authority(tmp_path: Path) -> None:
    request = v2_request(tmp_path / "state.db").model_copy(
        update={"pending_approval": {"capability_id": "creative.image.edit"}}
    )
    output = wire_stop(request)
    output["pending_approval_action"] = "cancel"

    runner = V2Runner(output)
    result = CodexReasoningProvider(runner, tmp_path, "fixture").plan_v2(request)

    assert request.pending_approval == {"capability_id": "creative.image.edit"}
    assert result.decision.pending_approval_action == "cancel"
    assert "set authorization_message" in runner.prompt
    assert "set pending_approval_action=preserve" in runner.prompt


def test_v2_stop_requires_a_candidate(tmp_path: Path) -> None:
    request = v2_request(tmp_path / "state.db")
    output = {**wire_stop(request), "completion_candidate": None}

    with pytest.raises(CodexReasoningError):
        _ = CodexReasoningProvider(V2Runner(output), tmp_path, "fixture").plan_v2(request)


def test_v2_candidate_for_another_task_is_rejected(tmp_path: Path) -> None:
    request = v2_request(tmp_path / "state.db")
    output = wire_stop(request)
    candidate = output["completion_candidate"]
    assert isinstance(candidate, dict)
    candidate["task_id"] = "other-task"

    with pytest.raises(CodexReasoningError):
        _ = CodexReasoningProvider(V2Runner(output), tmp_path, "fixture").plan_v2(request)
