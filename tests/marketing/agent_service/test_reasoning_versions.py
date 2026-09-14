from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningDecisionV2,
    decode_reasoning_decision,
)
from ads_booster.contracts.task_completion import CompletionCandidate

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def test_legacy_decision_has_exact_historical_payload_and_digest() -> None:
    payload: JsonObject = {
        "schema_version": "trace.reasoning-decision.v1",
        "action": "stop",
        "capability_id": None,
        "tool_input": None,
        "expected_outcome": "Answer",
        "reasoning_summary": "The answer",
        "proposed_action_kind": None,
        "proposed_brand_ref": None,
    }
    decoded = decode_reasoning_decision(payload)
    assert isinstance(decoded, ReasoningDecision)
    assert decoded.model_dump(mode="json") == payload
    assert contract_sha256(decoded) == contract_sha256(payload)


def test_v2_stop_requires_exact_bound_candidate() -> None:
    with pytest.raises(ValidationError, match="completion candidate"):
        _ = ReasoningDecisionV2(
            action="stop", expected_outcome="Answer", reasoning_summary="Candidate"
        )
    candidate = CompletionCandidate(
        candidate_id="candidate",
        task_id="task",
        task_revision=1,
        answer="Answer",
        answer_sha256=contract_sha256({"answer": "Answer"}),
    )
    decision = ReasoningDecisionV2(
        action="stop",
        expected_outcome="Answer",
        reasoning_summary="Candidate",
        completion_candidate=candidate,
    )
    assert decode_reasoning_decision(decision.model_dump(mode="json")) == decision
    with pytest.raises(ValidationError, match="digest mismatch"):
        _ = CompletionCandidate.model_validate({**candidate.model_dump(), "answer": "Changed"})


def test_v2_tool_decision_carries_current_request_authorization() -> None:
    decision = ReasoningDecisionV2(
        action="invoke_tool",
        capability_id="creative.image.edit",
        tool_input={"prompt": "blue background"},
        expected_outcome="Create the requested image",
        reasoning_summary="Creating the requested image",
        authorization_message="이 이미지 만들어줘",
    )

    assert decision.authorization_message == "이 이미지 만들어줘"
    assert decision.pending_approval_action == "preserve"
    assert decode_reasoning_decision(decision.model_dump(mode="json")) == decision


def test_candidate_digest_binds_links_and_attachment_references() -> None:
    original = CompletionCandidate(
        candidate_id="candidate",
        task_id="task",
        task_revision=1,
        answer="Answer",
        answer_sha256=contract_sha256({"answer": "Answer"}),
    )
    changed = original.model_copy(update={"attachment_refs": ("asset:new",)})
    assert contract_sha256(original) != contract_sha256(changed)
