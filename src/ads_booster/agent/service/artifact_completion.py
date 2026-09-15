from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.service.completion_evidence import CompletionEvidenceReader
from ads_booster.agent.service.task_completion import LegacyV1CompletionAssessor
from ads_booster.agent.service.task_drive import DecisionProjectionContext, candidate_from_evidence
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_progress import TaskCheckpoint
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.execution_control import ExecutionCancelledError

if TYPE_CHECKING:
    from ads_booster.agent.service.task_completion import TaskCompletionService
    from ads_booster.agent.service.task_progress import TaskProjection
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun, ToolReceiptRecord
    from ads_booster.contracts.task_completion import CompletionCandidate
    from ads_booster.contracts.task_progress import TaskSpec


def artifact_candidate(
    run: AgentRun,
    task: TaskProjection,
    receipt: ToolReceiptRecord,
    completion: TaskCompletionService | None,
) -> CompletionCandidate | None:
    if (
        completion is None
        or completion.assessor is None
        or isinstance(completion.assessor, LegacyV1CompletionAssessor)
        or completion.proof_reader is None
        or receipt.disposition != "succeeded"
        or task.checkpoint.assessment_calls + 1 >= task.checkpoint.policy.max_assessments
    ):
        return None
    records = _task_records(completion.repository.records(run.tenant_id, run.run_id), task.spec)
    reader = CompletionEvidenceReader(completion.repository)
    output_records = tuple(
        record
        for record in records
        if record.payload_schema_version == "trace.tool-output-evidence.v1"
    )
    receipt_digest = contract_sha256(receipt)
    latest = next(
        (
            record
            for record in reversed(output_records)
            if record.payload.get("receipt_sha256") == receipt_digest
        ),
        None,
    )
    if latest is None:
        return None
    bound = reader.read(run, latest)
    latest_candidate = candidate_from_evidence(
        task.spec, DecisionProjectionContext(run, (bound,)), answer="요청하신 결과물입니다."
    )
    if (
        bound.descriptor.effect_class is not EffectClass.LOCAL_ARTIFACT
        or not latest_candidate.attachment_refs
    ):
        return None
    try:
        if not completion.proof_reader.summarize(run, latest).verified:
            return None
    except ExecutionCancelledError:
        raise
    except OSError, ValueError, RuntimeError:
        return None
    previous = next(
        (
            candidate
            for record in reversed(records)
            if record.payload_schema_version == "trace.task-checkpoint.v1"
            and (candidate := TaskCheckpoint.model_validate(record.payload).candidate) is not None
        ),
        None,
    )
    candidate = candidate_from_evidence(
        task.spec,
        DecisionProjectionContext(
            run, tuple(reader.read(run, record) for record in output_records)
        ),
        answer=latest_candidate.answer if previous is None else previous.answer,
    )
    return candidate.model_copy(
        update={
            "candidate_id": f"artifact-candidate:{contract_sha256(receipt)}",
        }
    )


def _task_records(records: tuple[AgentRecord, ...], task: TaskSpec) -> tuple[AgentRecord, ...]:
    current = False
    selected: list[AgentRecord] = []
    for record in records:
        if record.payload_schema_version == "trace.task-spec.v1":
            current = (
                record.payload.get("task_id") == task.task_id
                and record.payload.get("task_revision") == task.task_revision
            )
        if current:
            selected.append(record)
    return tuple(selected)
