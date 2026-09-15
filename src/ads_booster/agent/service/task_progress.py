from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecord, AgentRecordKind, AgentRun, contract_sha256
from ads_booster.contracts.task_instruction import TaskInstruction
from ads_booster.contracts.task_progress import (
    TaskCheckpoint,
    TaskObligation,
    TaskPolicy,
    TaskProposal,
    TaskSpec,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class TaskProjection:
    spec: TaskSpec
    checkpoint: TaskCheckpoint

    def __post_init__(self) -> None:
        """Reject persisted checkpoints detached from their admitted task."""
        if (self.spec.task_id, self.spec.task_revision, contract_sha256(self.spec)) != (
            self.checkpoint.task_id,
            self.checkpoint.task_revision,
            self.checkpoint.spec_sha256,
        ):
            message = "task checkpoint spec binding mismatch"
            raise ValueError(message)
        ids = {item.obligation_id for item in self.spec.obligations}
        referenced = set(self.checkpoint.unresolved_obligation_ids) | {
            item.obligation_id for item in self.checkpoint.accepted_evidence
        }
        if not referenced.issubset(ids):
            message = "checkpoint references unknown obligation"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class AdmittedTaskRevision:
    source_event_id: str
    objective: str
    obligations: tuple[TaskObligation, ...]
    constraints: tuple[str, ...] = ()
    new_segment: bool = False
    new_task: bool = False
    policy: TaskPolicy | None = None


def seed_task(
    run: AgentRun, source_event_id: str | None = None, policy: TaskPolicy | None = None
) -> TaskProjection:
    source = source_event_id or f"initial:{contract_sha256(run.goal)[:32]}"
    task_id = f"task:{contract_sha256({'run': run.run_id, 'tenant': run.tenant_id})[:32]}"
    criteria = tuple(dict.fromkeys((run.goal.objective, *run.goal.success_criteria)))
    spec = TaskSpec(
        task_id=task_id,
        task_revision=1,
        source_event_id=source,
        source_event_ids=(source,),
        admitted_instructions=(TaskInstruction(source_event_id=source, text=run.goal.objective),),
        objective=run.goal.objective,
        original_objective=run.goal.objective,
        original_criteria=run.goal.success_criteria,
        obligations=tuple(
            TaskObligation(
                obligation_id=f"original-{index}",
                kind="response",
                description=criterion,
                source_refs=(source,),
            )
            for index, criterion in enumerate(criteria)
        ),
    )
    checkpoint = TaskCheckpoint(
        task_id=task_id,
        task_revision=1,
        spec_sha256=contract_sha256(spec),
        segment_id=f"segment:{contract_sha256({'source': source})[:32]}",
        policy=policy or TaskPolicy(),
        unresolved_obligation_ids=tuple(item.obligation_id for item in spec.obligations),
    )
    return TaskProjection(spec, checkpoint)


def apply_proposal(spec: TaskSpec, proposal: TaskProposal) -> TaskSpec:
    if (proposal.task_revision, proposal.source_event_id) != (
        spec.task_revision,
        spec.source_event_id,
    ):
        message = "task proposal source or revision mismatch"
        raise ValueError(message)
    existing = {item.obligation_id: item for item in spec.obligations}
    additions: list[TaskObligation] = []
    for obligation in proposal.obligations:
        if obligation.verification is not None:
            message = "actor proposal cannot select deterministic verification"
            raise ValueError(message)
        previous = existing.get(obligation.obligation_id)
        if previous is not None and previous != obligation:
            message = "actor proposal cannot replace or weaken an existing obligation"
            raise ValueError(message)
        if previous is None:
            additions.append(obligation)
    return TaskSpec.model_validate(
        {**spec.model_dump(), "obligations": (*spec.obligations, *additions)}
    )


def apply_admitted_revision(
    current: TaskProjection, admission: AdmittedTaskRevision
) -> TaskProjection:
    admission_sha256 = contract_sha256(
        {
            "source_event_id": admission.source_event_id,
            "objective": admission.objective,
            "obligations": [item.model_dump(mode="json") for item in admission.obligations],
            "constraints": list(admission.constraints),
            "new_segment": admission.new_segment,
            "new_task": admission.new_task,
            "policy": admission.policy.model_dump(mode="json") if admission.policy else None,
        }
    )
    if admission.source_event_id in current.spec.source_event_ids:
        if (
            dict(current.spec.source_admission_sha256s).get(admission.source_event_id)
            != admission_sha256
        ):
            message = "task admission idempotency conflict"
            raise ValueError(message)
        return current
    if admission.new_task and not admission.new_segment:
        message = "a new task requires a new admitted segment"
        raise ValueError(message)
    next_task_id = contract_sha256(
        {"prior": current.spec.task_id, "source": admission.source_event_id}
    )
    spec = TaskSpec(
        task_id=(f"task:{next_task_id[:32]}" if admission.new_task else current.spec.task_id),
        task_revision=1 if admission.new_task else current.spec.task_revision + 1,
        prior_revision=None if admission.new_task else current.spec.task_revision,
        source_event_id=admission.source_event_id,
        source_event_ids=(*current.spec.source_event_ids, admission.source_event_id),
        admitted_instructions=(
            *(current.spec.admitted_instructions if not admission.new_task else ()),
            TaskInstruction(source_event_id=admission.source_event_id, text=admission.objective),
        ),
        source_admission_sha256s=(
            *current.spec.source_admission_sha256s,
            (admission.source_event_id, admission_sha256),
        ),
        objective=admission.objective,
        prior_result=current.checkpoint.candidate
        if admission.new_task and current.checkpoint.disposition == "satisfied"
        else current.spec.prior_result,
        original_objective=admission.objective
        if admission.new_task
        else current.spec.original_objective,
        original_criteria=tuple(item.description for item in admission.obligations if item.required)
        if admission.new_task
        else current.spec.original_criteria,
        constraints=tuple(dict.fromkeys((*current.spec.constraints, *admission.constraints))),
        obligations=admission.obligations,
    )
    unchanged = {
        item.obligation_id for item in spec.obligations if item in current.spec.obligations
    }
    accepted = tuple(
        item for item in current.checkpoint.accepted_evidence if item.obligation_id in unchanged
    )
    changes: JsonObject = {
        "task_id": spec.task_id,
        "task_revision": spec.task_revision,
        "spec_sha256": contract_sha256(spec),
        "candidate": None,
        "disposition": "active",
        "next_action": "plan",
        "wait_reason": None,
        "accepted_evidence": [item.model_dump(mode="json") for item in accepted],
        "unresolved_obligation_ids": [
            item.obligation_id
            for item in spec.obligations
            if item.required
            and item.obligation_id not in {proof.obligation_id for proof in accepted}
        ],
    }
    if admission.new_segment:
        if current.checkpoint.disposition not in {
            "satisfied",
            "blocked",
            "budget_exhausted",
            "cancelled",
            "superseded",
        }:
            message = "active work cannot allocate a new segment"
            raise ValueError(message)
        changes.update(
            segment_id=f"segment:{contract_sha256({'source': admission.source_event_id})[:32]}",
            policy=(admission.policy or current.checkpoint.policy).model_dump(mode="json"),
            decision_calls=0,
            assessment_calls=0,
            active_elapsed_ms=0,
            fingerprints=[],
            reconsideration_used=False,
            strategy_feedback=None,
        )
    checkpoint = TaskCheckpoint.model_validate({**current.checkpoint.model_dump(), **changes})
    return TaskProjection(spec, checkpoint)


def task_records(
    run: AgentRun, projection: TaskProjection, now: datetime
) -> tuple[AgentRecord, ...]:
    return tuple(
        AgentRecord(
            schema_version="trace.agent-record.v1",
            record_id=f"task:{contract_sha256(payload)}:{run.revision}",
            run_id=run.run_id,
            kind=AgentRecordKind.EVIDENCE,
            payload_schema_version=payload.schema_version,
            payload=payload.model_dump(mode="json"),
            payload_sha256=contract_sha256(payload),
            occurred_at=now,
        )
        for payload in (projection.spec, projection.checkpoint)
    )


def project_task(run: AgentRun, records: tuple[AgentRecord, ...]) -> TaskProjection:
    specs: dict[str, TaskSpec] = {}
    checkpoint: TaskCheckpoint | None = None
    for record in records:
        if (
            record.run_id != run.run_id
            or record.kind is not AgentRecordKind.EVIDENCE
            or not record.record_id.startswith(f"task:{record.payload_sha256}:")
        ):
            continue
        if record.payload_schema_version == "trace.task-spec.v1":
            specs[record.payload_sha256] = TaskSpec.model_validate(record.payload)
        if record.payload_schema_version == "trace.task-checkpoint.v1":
            checkpoint = TaskCheckpoint.model_validate(record.payload)
    if checkpoint is None:
        return seed_task(run)
    spec = specs.get(checkpoint.spec_sha256)
    if spec is None:
        message = "task checkpoint has no canonical spec"
        raise ValueError(message)
    return TaskProjection(spec, checkpoint)
