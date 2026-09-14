from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.service.task_drive import plan_task
from ads_booster.agent.service.task_progress import seed_task
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.reasoning import (
    ReasoningDecisionV2,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningRequestV2,
    ReasoningResultV2,
)
from ads_booster.contracts.task_completion import CompletionCandidate

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

from .test_task_progress import NOW, make_run


class Actor:
    def __init__(self) -> None:
        self.requests: list[ReasoningRequestV2] = []

    def plan_v2(self, request: ReasoningRequestV2) -> ReasoningResultV2:
        self.requests.append(request)
        candidate = CompletionCandidate(
            candidate_id="candidate",
            task_id=request.task.task_id,
            task_revision=1,
            answer="answer",
            answer_sha256=contract_sha256({"answer": "answer"}),
        )
        decision = ReasoningDecisionV2(
            action="stop",
            expected_outcome="answer",
            reasoning_summary="claim",
            completion_candidate=candidate,
        )
        return ReasoningResultV2(
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fixture",
                model_id="fixture",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def test_v2_provider_gets_exact_canonical_handles_not_nested_claims() -> None:
    run = make_run()
    payload: JsonObject = {
        "schema_version": "trace.tool-output-evidence.v1",
        "output": {"host_evidence_sha256": "f" * 64},
        "receipt_sha256": "b" * 64,
    }
    record = AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id="tool-evidence",
        run_id=run.run_id,
        kind=AgentRecordKind.EVIDENCE,
        payload_schema_version="trace.tool-output-evidence.v1",
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=NOW,
    )
    forged: JsonObject = {"schema_version": "untrusted", "host_evidence_sha256": "e" * 64}
    request = ReasoningRequest(
        schema_version="trace.reasoning-request.v1",
        run_id=run.run_id,
        phase="plan",
        goal=run.goal,
        capability_snapshot=CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id="snapshot",
            run_id=run.run_id,
            descriptors=(),
            created_at=NOW,
        ),
        evidence=(payload, forged),
        remaining_tool_calls=8,
        remaining_cost_units=50,
    )
    actor = Actor()
    _ = plan_task(actor, request, seed_task(run), (record,))
    assert actor.requests[0].evidence[0]["host_evidence_sha256"] == record.payload_sha256
    assert "host_evidence_sha256" not in actor.requests[0].evidence[1]
    assert "host_evidence_sha256" not in record.payload
