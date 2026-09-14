# pyright: reportUnnecessaryComparison=false
"""Bounded operator projection for one durable Agent Run."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal, assert_never

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.agent.service.run_limits import remaining_budget
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import AgentRecord, AgentRun, AgentRunState
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.task_progress import TaskCheckpoint

if TYPE_CHECKING:
    from pathlib import Path

type ExecutionPhase = Literal["queued", "driving", "notifying", "waiting", "terminal"]
type QueueState = Literal["pending", "running", "notify", "notifying", "unknown"]
type TaskNextAction = Literal["plan", "execute", "assess", "wait", "done"]
type ProgressReason = Literal[
    "run_created",
    "task_admitted",
    "task_revised",
    "candidate_proposed",
    "decision_recorded",
    "assessment_recorded",
    "evidence_accepted",
    "obligation_satisfied",
    "work_advanced",
    "state_changed",
]

SafeOperatorCode = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]

_TABLE_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_QUEUE_ROW: TypeAdapter[tuple[str, str | None, str | None] | None] = TypeAdapter(
    tuple[str, str | None, str | None] | None
)
_QUEUE_STATE: TypeAdapter[QueueState] = TypeAdapter(QueueState)
_SAFE_CODE: TypeAdapter[SafeOperatorCode] = TypeAdapter(SafeOperatorCode)
_DATETIME: TypeAdapter[datetime] = TypeAdapter(datetime)


class CounterBudget(ContractModel):
    used: Annotated[int, Field(ge=0)]
    maximum: Annotated[int, Field(ge=0)]
    remaining: Annotated[int, Field(ge=0)]


class ToolExecutionBudget(ContractModel):
    calls: CounterBudget
    cost_units: CounterBudget


class ExecutionBudgets(ContractModel):
    decisions: CounterBudget
    assessments: CounterBudget
    tools: ToolExecutionBudget


class ExecutionQueueStatus(ContractModel):
    state: QueueState | None = None
    claim_owner: SafeOperatorCode | None = None
    lease_expires_at: datetime | None = None


class RunExecutionSummary(ContractModel):
    schema_version: Literal["trace.run-execution-summary.v1"] = "trace.run-execution-summary.v1"
    phase: ExecutionPhase
    next_action: TaskNextAction | None
    last_progress_at: datetime
    last_progress_reason: ProgressReason
    budgets: ExecutionBudgets
    queue: ExecutionQueueStatus
    wait_reason: SafeOperatorCode | None = None


def build_execution_summary(
    database_path: Path,
    run: AgentRun,
    records: tuple[AgentRecord, ...],
) -> RunExecutionSummary:
    """Project bounded execution state without copying raw ledger payloads."""
    task = project_task(run, records)
    tools = remaining_budget(run, records)
    queue = read_execution_queue(database_path, run.tenant_id, run.run_id)
    last_progress_at, last_progress_reason = _last_progress(run, records)
    return RunExecutionSummary(
        phase=_phase(run.state, queue.state),
        next_action=task.checkpoint.next_action,
        last_progress_at=last_progress_at,
        last_progress_reason=last_progress_reason,
        budgets=ExecutionBudgets(
            decisions=_counter(
                task.checkpoint.decision_calls,
                task.checkpoint.policy.max_decision_calls,
            ),
            assessments=_counter(
                task.checkpoint.assessment_calls,
                task.checkpoint.policy.max_assessments,
            ),
            tools=ToolExecutionBudget(
                calls=_counter(
                    run.budget.max_tool_calls - tools.tool_calls,
                    run.budget.max_tool_calls,
                ),
                cost_units=_counter(
                    run.budget.max_cost_units - tools.cost_units,
                    run.budget.max_cost_units,
                ),
            ),
        ),
        queue=queue,
        wait_reason=_safe_wait_reason(task.checkpoint.wait_reason),
    )


def read_execution_queue(
    database_path: Path,
    tenant_id: str,
    run_id: str,
) -> ExecutionQueueStatus:
    """Read current queue ownership when the installed schema provides it."""
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as db:
        table = _TABLE_ROW.validate_python(
            db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_drive_work'"
            ).fetchone()
        )
        if table is None:
            return ExecutionQueueStatus()
        try:
            row = _QUEUE_ROW.validate_python(
                db.execute(
                    """SELECT state,claim_owner,lease_expires_at FROM agent_drive_work
                    WHERE tenant_id=? AND run_id=?""",
                    (tenant_id, run_id),
                ).fetchone()
            )
        except sqlite3.OperationalError:
            row = _QUEUE_ROW.validate_python(
                db.execute(
                    """SELECT state,NULL,NULL FROM agent_drive_work
                    WHERE tenant_id=? AND run_id=?""",
                    (tenant_id, run_id),
                ).fetchone()
            )
    if row is None:
        return ExecutionQueueStatus()
    return ExecutionQueueStatus(
        state=_safe_queue_state(row[0]),
        claim_owner=_safe_optional_code(row[1]),
        lease_expires_at=_safe_utc_datetime(row[2]),
    )


def _counter(used: int, maximum: int) -> CounterBudget:
    return CounterBudget(used=used, maximum=maximum, remaining=max(0, maximum - used))


def _safe_queue_state(value: str) -> QueueState:
    try:
        return _QUEUE_STATE.validate_python(value)
    except ValidationError:
        return "unknown"


def _safe_optional_code(value: str | None) -> SafeOperatorCode | None:
    if value is None:
        return None
    try:
        return _SAFE_CODE.validate_python(value)
    except ValidationError:
        return None


def _safe_wait_reason(value: str | None) -> SafeOperatorCode | None:
    if value is None:
        return None
    return _safe_optional_code(value) or "details_in_run_record"


def _safe_utc_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = _DATETIME.validate_python(value)
    except ValidationError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _last_progress(
    run: AgentRun,
    records: tuple[AgentRecord, ...],
) -> tuple[datetime, ProgressReason]:
    last_at = run.created_at
    last_reason: ProgressReason = "run_created"
    previous: TaskCheckpoint | None = None
    for record in records:
        if record.payload_schema_version != "trace.task-checkpoint.v1":
            continue
        current = TaskCheckpoint.model_validate(record.payload)
        reason = _checkpoint_progress_reason(previous, current)
        previous = current
        if reason is not None:
            last_at, last_reason = record.occurred_at, reason
    return last_at, last_reason


def _checkpoint_progress_reason(
    previous: TaskCheckpoint | None,
    current: TaskCheckpoint,
) -> ProgressReason | None:
    reason: ProgressReason | None = None
    if previous is None:
        reason = "task_admitted"
    else:
        checks: tuple[tuple[bool, ProgressReason], ...] = (
            (
                (previous.task_id, previous.task_revision, previous.segment_id)
                != (current.task_id, current.task_revision, current.segment_id),
                "task_revised",
            ),
            (
                len(current.accepted_evidence) > len(previous.accepted_evidence)
                or len(current.unresolved_obligation_ids) < len(previous.unresolved_obligation_ids)
                or len(current.progress_obligation_ids) > len(previous.progress_obligation_ids),
                "obligation_satisfied",
            ),
            (
                len(current.progress_evidence_sha256s) > len(previous.progress_evidence_sha256s),
                "evidence_accepted",
            ),
            (previous.candidate != current.candidate, "candidate_proposed"),
            (current.assessment_calls > previous.assessment_calls, "assessment_recorded"),
            (current.decision_calls > previous.decision_calls, "decision_recorded"),
            (current.active_elapsed_ms > previous.active_elapsed_ms, "work_advanced"),
            (
                (previous.disposition, previous.next_action)
                != (current.disposition, current.next_action),
                "state_changed",
            ),
        )
        reason = next((candidate for matched, candidate in checks if matched), None)
    return reason


def _phase(state: AgentRunState, queue_state: QueueState | None) -> ExecutionPhase:
    match queue_state:
        case "pending":
            return "queued"
        case "running":
            return "driving"
        case "notify" | "notifying":
            return "notifying"
        case "unknown" | None:
            pass
        case unreachable:
            assert_never(unreachable)
    match state:
        case (
            AgentRunState.CREATED
            | AgentRunState.RUNNING
            | AgentRunState.AWAITING_TOOL
            | AgentRunState.AWAITING_RECONCILIATION
        ):
            return "driving"
        case AgentRunState.AWAITING_APPROVAL | AgentRunState.AWAITING_INPUT | AgentRunState.BLOCKED:
            return "waiting"
        case AgentRunState.COMPLETED | AgentRunState.STOPPED | AgentRunState.FAILED:
            return "terminal"
        case unreachable:
            assert_never(unreachable)
