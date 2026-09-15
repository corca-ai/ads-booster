from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ads_booster.contracts.agent_run import AgentIntent

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.task_completion import TaskCompletionService
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.contracts.tool_handoff import ToolInputHandoff


@runtime_checkable
class ToolHandoffReader(Protocol):
    def input_handoff(
        self, run: AgentRun, record: AgentRecord, now: datetime
    ) -> ToolInputHandoff | None: ...


def tool_input_handoff(
    run: AgentRun, record: AgentRecord, completion: TaskCompletionService | None, *, now: datetime
) -> AgentIntent | None:
    if completion is None or not isinstance(completion.proof_reader, ToolHandoffReader):
        return None
    handoff = completion.proof_reader.input_handoff(run, record, now)
    if handoff is None:
        return None
    return AgentIntent(
        schema_version="trace.agent-intent.v1",
        intent_id=f"{run.run_id}:intent:{run.revision}",
        run_id=run.run_id,
        step_id=f"{run.run_id}:step:{run.revision}",
        action="request_input",
        evidence_sha256s=(record.payload_sha256,),
        expected_outcome=handoff.expected_outcome,
        reasoning_summary=handoff.question,
    )
