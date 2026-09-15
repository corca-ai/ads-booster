from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.agent.service.sqlite_repository import AgentRunConflictError
from ads_booster.agent.service.task_progress import (
    AdmittedTaskRevision,
    TaskProjection,
    apply_admitted_revision,
    project_task,
)
from ads_booster.contracts.agent_run import AgentRecord, AgentRunState, contract_sha256
from ads_booster.contracts.task_progress import TaskObligation

if TYPE_CHECKING:
    from sqlite3 import Connection

    from ads_booster.agent.service.sqlite_repository import RepositoryAdmission
    from ads_booster.contracts.agent_run import AgentRun

_RECORD_ROWS = TypeAdapter(list[tuple[str]])


def recoverable_task(task: TaskProjection) -> bool:
    return task.checkpoint.wait_reason in {
        "no_progress",
        "completion_unsatisfied",
        "verification_unavailable",
        "decision_budget_exhausted",
    }


def admit_task_revision(
    run: AgentRun, current: TaskProjection, event_id: str, note: str
) -> TaskProjection:
    terminal = run.state in {
        AgentRunState.COMPLETED,
        AgentRunState.STOPPED,
        AgentRunState.FAILED,
        AgentRunState.BLOCKED,
    }
    obligation = TaskObligation(
        obligation_id=f"request:{contract_sha256({'event': event_id})[:24]}",
        kind="response",
        description=note,
        source_refs=(event_id,),
    )
    return apply_admitted_revision(
        current,
        AdmittedTaskRevision(
            source_event_id=event_id,
            objective=note,
            obligations=(obligation,) if terminal else (*current.spec.obligations, obligation),
            new_segment=terminal,
            new_task=terminal,
        ),
    )


def completion_revision_fence(run: AgentRun, expected: TaskProjection) -> RepositoryAdmission:
    def admission(connection: Connection) -> None:
        rows = _RECORD_ROWS.validate_python(
            connection.execute(
                "SELECT record_json FROM agent_records WHERE run_id=? ORDER BY rowid",
                (run.run_id,),
            ).fetchall()
        )
        records = tuple(AgentRecord.model_validate_json(row[0]) for row in rows)
        prior = tuple(
            record
            for record in records
            if not record.record_id.startswith("task:")
            or not record.record_id.endswith(f":{run.revision}")
        )
        latest = project_task(run, prior)
        if (
            latest.spec != expected.spec
            or latest.checkpoint.candidate != expected.checkpoint.candidate
        ):
            message = "completion_task_revision_conflict"
            raise AgentRunConflictError(message)

    return admission


def task_at_boundary(task: TaskProjection, state: AgentRunState, reason: str) -> TaskProjection:
    if state is AgentRunState.RUNNING:
        return TaskProjection(
            task.spec,
            task.checkpoint.model_copy(
                update={
                    "disposition": "active",
                    "wait_reason": None,
                    "next_action": "plan"
                    if task.checkpoint.next_action == "wait"
                    else task.checkpoint.next_action,
                }
            ),
        )
    if state is AgentRunState.COMPLETED:
        return task
    disposition = (
        "cancelled"
        if state is AgentRunState.STOPPED
        else ("blocked" if state in {AgentRunState.BLOCKED, AgentRunState.FAILED} else "waiting")
    )
    if state is AgentRunState.BLOCKED and task.checkpoint.disposition == "budget_exhausted":
        disposition = "budget_exhausted"
    return TaskProjection(
        task.spec,
        task.checkpoint.model_copy(
            update={
                "disposition": disposition,
                "next_action": "wait",
                "wait_reason": state.value
                if state
                in {
                    AgentRunState.AWAITING_TOOL,
                    AgentRunState.AWAITING_APPROVAL,
                    AgentRunState.AWAITING_RECONCILIATION,
                }
                else reason,
            }
        ),
    )
