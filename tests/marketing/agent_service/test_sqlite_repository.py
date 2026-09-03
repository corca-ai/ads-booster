from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecord,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    contract_sha256,
)
from ads_booster.marketing.agent_service.sqlite_repository import (
    AgentRunConflictError,
    SqliteAgentRunRepository,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_repository_restarts_from_the_same_canonical_run(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    first = SqliteAgentRunRepository(database)
    created = first.create(_run())
    observed = first.append_step(
        created,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=1,
        records=(_record(),),
    )

    restarted = SqliteAgentRunRepository(database)
    loaded = restarted.get("trace", "run-one")

    assert loaded == observed
    assert restarted.steps("trace", "run-one") == (
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
    )
    assert restarted.records("trace", "run-one") == (_record(),)


def test_create_is_idempotent_only_for_the_exact_same_run(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "agent-service.sqlite3")
    original = repository.create(_run())

    duplicate = repository.create(_run())

    assert duplicate == original
    with pytest.raises(AgentRunConflictError, match="agent_run_idempotency_conflict"):
        _ = repository.create(
            _run().model_copy(
                update={
                    "goal": AgentGoal(
                        objective="Different objective",
                        success_criteria=("learn",),
                    )
                }
            )
        )


def test_append_rejects_stale_revision_and_broken_step_chain(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "agent-service.sqlite3")
    run = repository.create(_run())
    first = repository.append_step(
        run,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=1,
    )

    with pytest.raises(AgentRunConflictError, match="agent_run_revision_conflict"):
        _ = repository.append_step(
            run,
            _step(sequence=1, kind=AgentStepKind.PLAN, parent=None),
            state=AgentRunState.RUNNING,
            expected_revision=1,
        )
    with pytest.raises(AgentRunConflictError, match="agent_step_parent_conflict"):
        _ = repository.append_step(
            first,
            _step(sequence=2, kind=AgentStepKind.PLAN, parent="f" * 64),
            state=AgentRunState.RUNNING,
            expected_revision=2,
        )


def test_append_only_events_rebuild_and_detect_projection_tampering(tmp_path: Path) -> None:
    database = tmp_path / "agent-service.sqlite3"
    repository = SqliteAgentRunRepository(database)
    created = repository.create(_run())
    updated = repository.append_step(
        created,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=1,
    )

    assert repository.rebuild("trace", "run-one") == updated
    with closing(sqlite3.connect(database)) as connection:
        _ = connection.execute(
            "UPDATE agent_runs SET run_json = ? WHERE run_id = ?",
            (_run().model_dump_json(), "run-one"),
        )
        connection.commit()

    with pytest.raises(AgentRunConflictError, match="agent_run_projection_mismatch"):
        _ = repository.get("trace", "run-one")


def test_create_request_digest_stays_idempotent_after_run_progress(tmp_path: Path) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "agent-service.sqlite3")
    original = _run()
    request_sha256 = "f" * 64
    created = repository.create(original, request_sha256=request_sha256)
    progressed = repository.append_step(
        created,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=1,
    )

    duplicate = repository.create(original, request_sha256=request_sha256)

    assert duplicate == progressed


def _run() -> AgentRun:
    return AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="run-one",
        tenant_id="trace",
        goal=AgentGoal(objective="Find a stronger launch format", success_criteria=("learn",)),
        budget=AgentBudget(max_tool_calls=5, max_cost_units=20),
        created_at=NOW,
        updated_at=NOW,
    )


def _step(
    *,
    sequence: int,
    kind: AgentStepKind,
    parent: str | None,
) -> AgentStep:
    return AgentStep(
        schema_version="trace.agent-step.v1",
        step_id=f"step-{sequence}",
        run_id="run-one",
        sequence=sequence,
        kind=kind,
        state="completed",
        input_sha256=chr(ord("a") + sequence - 1) * 64,
        output_sha256=chr(ord("b") + sequence - 1) * 64,
        parent_step_sha256=parent,
        occurred_at=NOW + timedelta(seconds=sequence),
    )


def _record() -> AgentRecord:
    payload: JsonObject = {
        "schema_version": "trace.test-evidence.v1",
        "summary": "No Appium required",
    }
    return AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id="evidence-one",
        run_id="run-one",
        kind=AgentRecordKind.EVIDENCE,
        payload_schema_version="trace.test-evidence.v1",
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=NOW + timedelta(seconds=1),
    )
