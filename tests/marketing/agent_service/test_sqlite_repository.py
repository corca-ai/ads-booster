from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.agent.service.record_cache import RecordValidationCache
from ads_booster.agent.service.sqlite_repository import (
    AgentRunConflictError,
    SqliteAgentRunRepository,
)
from ads_booster.contracts import agent_run
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

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_cache_validates_changed_json_and_isolates_concurrent_returns() -> None:
    record = _record()
    raw = record.model_dump_json()
    cache = RecordValidationCache()
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = tuple(pool.map(cache.parse, (raw,) * 12))
    records[0].payload["summary"] = "mutated"
    assert all(item == record for item in records[1:])
    assert cache.parse(raw) == record
    changed = record.model_copy(update={"payload_sha256": "0" * 64}).model_dump_json()
    with pytest.raises(ValidationError, match="payload digest mismatch"):
        _ = cache.parse(changed)


@pytest.mark.parametrize("max_bytes", [1, 1024 * 1024])
def test_cache_evicts_at_byte_and_entry_limits(
    max_bytes: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = RecordValidationCache(max_bytes=max_bytes, max_entries=1)
    first = _record()
    second = first.model_copy(update={"record_id": "second"})
    hashes: list[JsonObject] = []
    original = agent_run.contract_sha256

    def count_hash(value: JsonObject) -> str:
        hashes.append(value)
        return original(value)

    monkeypatch.setattr(agent_run, "contract_sha256", count_hash)
    assert cache.parse(first.model_dump_json()) == first
    assert cache.parse(second.model_dump_json()) == second
    assert cache.parse(first.model_dump_json()) == first
    assert len(hashes) == 3


def test_repository_cache_observes_another_writer_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    reader = SqliteAgentRunRepository(path)
    run = reader.create(_run())
    assert reader.records("trace", run.run_id) == ()
    writer = SqliteAgentRunRepository(path)
    run = writer.append_step(
        run,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=run.revision,
        records=(_record(),),
    )
    assert reader.records("trace", run.run_id) == (_record(),)
    payload: JsonObject = {"schema_version": "trace.test-evidence.v1", "summary": "new event"}
    second = _record().model_copy(
        update={
            "record_id": "second",
            "payload": payload,
            "payload_sha256": contract_sha256(payload),
        }
    )
    _ = writer.append_step(
        run,
        _step(sequence=2, kind=AgentStepKind.OBSERVE, parent=run.head_step_sha256),
        state=AgentRunState.RUNNING,
        expected_revision=run.revision,
        records=(second,),
    )
    assert reader.records("trace", run.run_id) == (_record(), second)
    assert SqliteAgentRunRepository(path).records("trace", run.run_id) == (_record(), second)


def test_repeated_reads_validate_unchanged_records_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqliteAgentRunRepository(tmp_path / "state.db")
    created = repository.create(_run())
    _ = repository.append_step(
        created,
        _step(sequence=1, kind=AgentStepKind.OBSERVE, parent=None),
        state=AgentRunState.RUNNING,
        expected_revision=1,
        records=(_record(),),
    )
    hashes: list[JsonObject] = []
    original = agent_run.contract_sha256

    def count_hash(value: JsonObject) -> str:
        hashes.append(value)
        return original(value)

    monkeypatch.setattr(agent_run, "contract_sha256", count_hash)
    first = repository.records("trace", "run-one")
    first[0].payload["unexpected"] = True
    second = repository.records("trace", "run-one")
    assert len(hashes) == 1
    assert second == (_record(),)
    assert repository.records("another-tenant", "run-one") == ()


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
