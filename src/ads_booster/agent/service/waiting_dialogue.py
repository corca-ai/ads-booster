"""Response-only reasoning while a canonical operation awaits reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.service.deferred_failure import FAILURE_TEXT
from ads_booster.agent.service.task_drive import plan_task
from ads_booster.agent.service.task_progress import seed_task
from ads_booster.contracts.agent_run import AgentRecordKind, CapabilitySnapshot, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningDecisionV2, ReasoningRequest

if TYPE_CHECKING:
    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.contracts.agent_run import AgentGoal, AgentRun
    from ads_booster.transport.json_types import JsonObject, JsonValue


def answer_waiting_dialogue(
    service: MarketingAgentService, run: AgentRun, goal: AgentGoal
) -> tuple[str, JsonObject]:
    """Read a scoped Run under its caller's lock without advancing or revising it."""
    records = service.repository.records(run.tenant_id, run.run_id)
    invocation = next(
        (record for record in reversed(records) if record.kind is AgentRecordKind.INVOCATION),
        None,
    )
    receipts: list[JsonValue] = [
        {
            "invocation_sha256": record.payload.get("invocation_sha256"),
            "disposition": record.payload.get("disposition"),
            "output_sha256": record.payload.get("output_sha256"),
        }
        for record in records
        if record.kind is AgentRecordKind.RECEIPT
    ]
    pending: JsonObject = {
        "run_id": run.run_id,
        "state": run.state.value,
        "revision": run.revision,
        "objective": run.goal.objective,
        "invocation_sha256": invocation.payload_sha256 if invocation else None,
        "capability_id": invocation.payload.get("capability_id") if invocation else None,
        "receipts": receipts[-16:],
        "verification": "persisted_records_only_no_external_lookup",
    }
    diagnostic = next(
        (
            record.payload.get("reason_code")
            for record in reversed(records)
            if record.payload_schema_version == "trace.deferred-provider-failure.v1"
        ),
        None,
    )
    if isinstance(diagnostic, str) and diagnostic in FAILURE_TEXT:
        pending["failure_code"] = diagnostic
        pending["failure_reason"] = FAILURE_TEXT[diagnostic]
    dialogue_goal = goal.model_copy(update={"context": {**goal.context, "pending_work": pending}})
    dialogue_id = (
        "dialogue-"
        + contract_sha256(
            {"tenant": run.tenant_id, "run": run.run_id, "goal": goal.model_dump(mode="json")}
        )[:40]
    )
    request = ReasoningRequest(
        schema_version="trace.reasoning-request.v1",
        run_id=dialogue_id,
        phase="plan",
        goal=dialogue_goal,
        current_user_message=goal.objective,
        capability_snapshot=CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id=dialogue_id + ":capabilities",
            run_id=dialogue_id,
            descriptors=(),
            created_at=run.updated_at,
        ),
        remaining_tool_calls=0,
        remaining_cost_units=0,
    )
    task = seed_task(run.model_copy(update={"run_id": dialogue_id, "goal": dialogue_goal}))
    result = plan_task(service.reasoning, request, task, ())
    match result.decision.action:
        case "invoke_tool":
            message = "waiting_dialogue_tool_execution_forbidden"
            raise ValueError(message)
        case "stop" | "request_input":
            match result.decision:
                case ReasoningDecisionV2(completion_candidate=candidate) if candidate is not None:
                    answer = candidate.answer
                case ReasoningDecisionV2() | ReasoningDecision():
                    answer = result.decision.reasoning_summary
    return answer, result.model_dump(mode="json")
