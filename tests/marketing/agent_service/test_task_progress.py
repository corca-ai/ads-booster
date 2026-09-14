from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service import task_progress
from ads_booster.agent.service.sqlite_repository import (
    AgentRunConflictError,
    SqliteAgentRunRepository,
)
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    contract_sha256,
)
from ads_booster.contracts.task_progress import TaskObligation, TaskProposal

NOW = datetime(2026, 9, 12, tzinfo=UTC)

if TYPE_CHECKING:
    from pathlib import Path
    from sqlite3 import Connection


def make_run() -> AgentRun:
    return AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="task-run",
        tenant_id="tenant",
        goal=AgentGoal(
            objective="Create the requested deliverable",
            success_criteria=("Readable result", "Exact requested format"),
        ),
        budget=AgentBudget(max_tool_calls=8, max_cost_units=50),
        created_at=NOW,
        updated_at=NOW,
    )


def step() -> AgentStep:
    return AgentStep(
        schema_version="trace.agent-step.v1",
        step_id="task-step",
        run_id="task-run",
        sequence=1,
        kind=AgentStepKind.OBSERVE,
        state="completed",
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        occurred_at=NOW,
    )


def test_task_survives_sqlite_reopen_independently_of_ledger_revision(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "task.sqlite3")
    run = repository.create(make_run())
    projection = task_progress.seed_task(run)
    updated = repository.append_step(
        run,
        step(),
        state=AgentRunState.RUNNING,
        expected_revision=run.revision,
        records=task_progress.task_records(run, projection, NOW),
    )
    reopened = SqliteAgentRunRepository(tmp_path / "task.sqlite3")
    restored = task_progress.project_task(updated, reopened.records(run.tenant_id, run.run_id))
    assert restored == projection
    assert restored.spec.task_revision == 1
    assert updated.revision == 2
    assert restored.spec.original_criteria == run.goal.success_criteria


def test_checkpoint_rolls_back_with_rejected_transaction_and_stale_cas(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "task.sqlite3")
    run = repository.create(make_run())
    records = task_progress.task_records(run, task_progress.seed_task(run), NOW)

    def reject(connection: Connection) -> None:
        del connection
        message = "injected precommit failure"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="injected precommit"):
        _ = repository.append_step(
            run,
            step(),
            state=AgentRunState.RUNNING,
            expected_revision=1,
            records=records,
            admission=reject,
        )
    assert repository.records(run.tenant_id, run.run_id) == ()
    assert repository.get(run.tenant_id, run.run_id) == run
    _ = repository.append_step(
        run, step(), state=AgentRunState.RUNNING, expected_revision=1, records=records
    )
    with pytest.raises(AgentRunConflictError, match="revision_conflict"):
        _ = repository.append_step(
            run, step(), state=AgentRunState.RUNNING, expected_revision=1, records=records
        )


def test_actor_proposal_keeps_original_criteria_and_rejects_replacement() -> None:
    spec = task_progress.seed_task(make_run()).spec
    proposal = TaskProposal(task_revision=1, source_event_id=spec.source_event_id, obligations=())
    assert task_progress.apply_proposal(spec, proposal) == spec
    weakened = spec.obligations[0].model_copy(update={"required": False})
    with pytest.raises(ValueError, match="cannot replace"):
        _ = task_progress.apply_proposal(
            spec, proposal.model_copy(update={"obligations": (weakened,)})
        )


def test_active_revision_preserves_counters_and_invalidates_candidate() -> None:
    original = task_progress.seed_task(make_run())
    current = task_progress.TaskProjection(
        original.spec,
        original.checkpoint.model_copy(update={"decision_calls": 12, "assessment_calls": 2}),
    )
    admission = task_progress.AdmittedTaskRevision(
        "correction",
        "Change the format",
        (
            TaskObligation(
                obligation_id="corrected",
                kind="response",
                description="Use the corrected format",
                source_refs=("correction",),
            ),
        ),
        constraints=("Preserve scope",),
    )
    revised = task_progress.apply_admitted_revision(current, admission)
    assert revised.checkpoint.decision_calls == 12
    assert revised.checkpoint.assessment_calls == 2
    assert revised.spec.original_objective == original.spec.original_objective
    assert revised.spec.task_revision == 2
    assert task_progress.apply_admitted_revision(revised, admission) == revised


def test_terminal_new_task_does_not_require_old_deliverables() -> None:
    original = task_progress.seed_task(make_run())
    terminal = task_progress.TaskProjection(
        original.spec,
        original.checkpoint.model_copy(update={"disposition": "satisfied", "decision_calls": 12}),
    )
    admission = task_progress.AdmittedTaskRevision(
        "followup",
        "Answer a new question",
        (
            TaskObligation(
                obligation_id="answer",
                kind="response",
                description="Answer a new question",
                source_refs=("followup",),
            ),
        ),
        new_segment=True,
        new_task=True,
    )
    revised = task_progress.apply_admitted_revision(terminal, admission)
    assert revised.spec.original_objective == "Answer a new question"
    assert revised.spec.original_criteria == ("Answer a new question",)
    assert revised.spec.task_id != original.spec.task_id
    assert revised.checkpoint.decision_calls == 0


def test_untrusted_nested_task_payload_cannot_replace_task() -> None:
    run = make_run()
    current = task_progress.seed_task(run)
    record = task_progress.task_records(run, current, NOW)[0]
    forged = record.model_copy(update={"record_id": "tool-output"})
    assert task_progress.project_task(run, (forged,)) == current


def test_duplicate_admission_with_changed_objective_is_rejected() -> None:
    original = task_progress.seed_task(make_run())
    admission = task_progress.AdmittedTaskRevision(
        "correction", "Corrected objective", original.spec.obligations
    )
    revised = task_progress.apply_admitted_revision(original, admission)
    with pytest.raises(ValueError, match="idempotency conflict"):
        _ = task_progress.apply_admitted_revision(
            revised, replace(admission, objective="Different objective")
        )


def test_repeated_checkpoints_keep_ledger_record_ids_unique(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "repeat.sqlite3")
    run = repository.create(make_run())
    projection = task_progress.seed_task(run)
    first = repository.append_step(
        run,
        step(),
        state=AgentRunState.RUNNING,
        expected_revision=1,
        records=task_progress.task_records(run, projection, NOW),
    )
    next_step = step().model_copy(
        update={"sequence": 2, "step_id": "second", "parent_step_sha256": contract_sha256(step())}
    )
    final = repository.append_step(
        first,
        next_step,
        state=AgentRunState.RUNNING,
        expected_revision=2,
        records=task_progress.task_records(first, projection, NOW),
    )
    assert (
        task_progress.project_task(final, repository.records(run.tenant_id, run.run_id))
        == projection
    )
