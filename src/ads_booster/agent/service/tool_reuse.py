from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.service.completion_evidence import CompletionEvidenceReader
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.execution_control import ExecutionCancelledError

if TYPE_CHECKING:
    from ads_booster.agent.service.task_completion import TaskCompletionService
    from ads_booster.contracts.agent_run import (
        AgentRecord,
        AgentRun,
        ToolInvocation,
        ToolReceiptRecord,
    )
    from ads_booster.contracts.tool_capability import ToolDescriptor


def reusable_tool_receipt(
    run: AgentRun,
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    completion: TaskCompletionService | None,
) -> ToolReceiptRecord | None:
    if (
        completion is None
        or completion.proof_reader is None
        or descriptor.effect_class is EffectClass.OBSERVE
        or descriptor.idempotency.key_scope != "run_tool_input"
    ):
        return None
    records = completion.repository.records(run.tenant_id, run.run_id)
    current_spec = contract_sha256(project_task(run, records).spec)
    spec_digest = ""
    candidates: list[AgentRecord] = []
    for record in records:
        if record.payload_schema_version == "trace.task-spec.v1":
            spec_digest = record.payload_sha256
        if (
            record.payload_schema_version == "trace.tool-output-evidence.v1"
            and record.payload.get("capability_id") == descriptor.capability_id
            and spec_digest == current_spec
        ):
            candidates.append(record)
    for record in reversed(candidates):
        bound = CompletionEvidenceReader(completion.repository).read(run, record)
        if (
            bound.invocation.idempotency_key == invocation.idempotency_key
            and bound.invocation.input == invocation.input
            and bound.descriptor.version == descriptor.version
            and bound.descriptor.owner == descriptor.owner
            and bound.descriptor.installation_id == descriptor.installation_id
            and bound.descriptor.effect_class == descriptor.effect_class
            and bound.receipt.disposition == "succeeded"
        ):
            try:
                verified = completion.proof_reader.summarize(run, record).verified
            except ExecutionCancelledError:
                raise
            except (OSError, ValueError, RuntimeError):
                return None
            if verified:
                return bound.receipt
    return None
