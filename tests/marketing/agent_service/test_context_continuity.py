from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


class HumanThenToolsReasoning(AskThenStopReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        count = len(self.requests)
        invokes = count in {2, 3}
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool" if invokes else "request_input" if count == 1 else "stop",
                capability_id="research.web" if invokes else None,
                tool_input={"query": f"reference {count}"} if invokes else None,
                expected_outcome="Preserve the character while comparing background alternatives",
                reasoning_summary="Need the existing design" if count == 1 else "Compare sources",
            ),
        )


def test_human_constraints_and_both_tool_sources_reach_final_decision(tmp_path: Path) -> None:
    reasoning = HumanThenToolsReasoning()
    service = _service(tmp_path / "agent.sqlite3", reasoning)
    waiting = service.create(_request(), now=NOW)
    completed = service.submit_input(
        "trace",
        waiting.run_id,
        {"preserve": "character and calendar", "change": "top margin"},
        now=NOW,
    )
    assert completed.state is AgentRunState.COMPLETED
    evidence = reasoning.requests[-1].evidence
    assert len(evidence) == 3
    assert evidence[0]["evidence"] == {
        "preserve": "character and calendar",
        "change": "top margin",
    }
    assert [item["capability_id"] for item in evidence[1:]] == ["research.web", "research.web"]
    assert evidence[1]["receipt_sha256"] != evidence[2]["receipt_sha256"]


def test_verified_tool_restart_retains_earlier_human_input(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    service = _service(database, HumanThenToolsReasoning())
    waiting = service.create(_request(), now=NOW)

    def crash(point: str) -> None:
        if point == "verify_committed":
            message = "fixture interrupted after verified tool"
            raise RuntimeError(message)

    service.fault_hook = crash
    with pytest.raises(RuntimeError, match="fixture interrupted"):
        _ = service.submit_input("trace", waiting.run_id, {"preserve": "calendar"}, now=NOW)
    reasoning = AskThenStopReasoning(stop=True)
    restarted = _service(database, reasoning)
    assert restarted.drive("trace", waiting.run_id, now=NOW).state is AgentRunState.COMPLETED
    assert len(reasoning.requests[-1].evidence) == 2
    assert reasoning.requests[-1].evidence[0]["evidence"] == {"preserve": "calendar"}


@pytest.mark.parametrize("tenant", ["trace", "another-workspace"])
def test_context_selection_does_not_cross_run_or_workspace(tmp_path: Path, tenant: str) -> None:
    reasoning = AskThenStopReasoning()
    service = _service(tmp_path / "agent.sqlite3", reasoning)
    waiting = service.create(_request(), now=NOW)
    _ = service.submit_input("trace", waiting.run_id, {"private_note": "synthetic"}, now=NOW)
    other = _request().model_copy(update={"run_id": "other-run", "tenant_id": tenant})
    _ = service.create(other, now=NOW)
    assert reasoning.requests[-1].evidence == ()
    assert reasoning.requests[-1].phase == "plan"


class KeepAskingReasoning(AskThenStopReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="request_input",
                expected_outcome="Continue the same human collaboration",
                reasoning_summary="Provide the next comparison result",
            ),
        )


@pytest.mark.parametrize("text", ["small", "合成テスト" * 3000], ids=["record-limit", "byte-limit"])
def test_projection_is_bounded_and_auditable_without_deleting_history(
    tmp_path: Path,
    text: str,
) -> None:
    reasoning = KeepAskingReasoning()
    service = _service(tmp_path / "agent.sqlite3", reasoning)
    waiting = service.create(_request(), now=NOW)
    for version in range(40):
        _ = service.submit_input(
            "trace",
            waiting.run_id,
            {"version": version, "synthetic_note": text},
            now=NOW,
        )
    records = service.repository.records("trace", waiting.run_id)
    originals = [r for r in records if r.payload_schema_version == "trace.agent-input-evidence.v1"]
    assert len(originals) == 40
    selections = [
        r for r in records if r.payload_schema_version == "trace.reasoning-context-selection.v1"
    ]
    assert selections
    selection = selections[-1].payload
    assert isinstance(selection["omitted_count"], int)
    assert isinstance(selection["selected_bytes"], int)
    assert selection["omitted_count"] > 0
    assert selection["selected_bytes"] <= 49152
    evidence = reasoning.requests[-1].evidence
    assert len(evidence) <= 33
    assert evidence[-1]["schema_version"] == "trace.reasoning-context-omission.v1"
    assert selection["selected_sha256s"] == [contract_sha256(item) for item in evidence[:-1]]
    assert all(r.kind is AgentRecordKind.EVIDENCE for r in originals)
    assert "39" in str(evidence[:-1])
