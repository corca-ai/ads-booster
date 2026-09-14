from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service import run_limits
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_progress import (
    TaskProjection,
    project_task,
    seed_task,
    task_records,
)
from ads_booster.bootstrap import completion_policy
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentRecord,
    AgentRecordKind,
    AgentRunState,
    ToolReceiptRecord,
    contract_sha256,
)

from .test_task_progress import NOW, make_run, step

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def test_decision_reservation_cannot_reset_at_assessment_limit() -> None:
    checkpoint = seed_task(make_run()).checkpoint
    for _ in range(3):
        reserved = run_limits.reserve_decision(checkpoint, assessment=True)
        assert reserved is not None
        checkpoint = reserved
    assert checkpoint.decision_calls == 3
    assert checkpoint.assessment_calls == 3
    assert run_limits.reserve_decision(checkpoint, assessment=True) is None


def test_alternating_failure_cycle_reconsiders_once_then_blocks() -> None:
    checkpoint = seed_task(make_run()).checkpoint
    for fingerprint in ("a", "b", "a", "b", "a", "b"):
        checkpoint = run_limits.observe_progress(
            checkpoint, run_limits.ProgressObservation(fingerprint * 64)
        )
    assert checkpoint.reconsideration_used is True
    assert checkpoint.disposition == "active"
    checkpoint = run_limits.observe_progress(checkpoint, run_limits.ProgressObservation("a" * 64))
    assert checkpoint.disposition == "blocked"
    assert checkpoint.wait_reason == "no_progress"


def test_wait_and_new_evidence_do_not_count_as_repeated_failure() -> None:
    checkpoint = seed_task(make_run()).checkpoint
    observation = run_limits.ProgressObservation("a" * 64, waiting=True)
    assert run_limits.observe_progress(checkpoint, observation) == checkpoint
    for index in range(5):
        checkpoint = run_limits.observe_progress(
            checkpoint,
            run_limits.ProgressObservation("a" * 64, evidence_sha256s=(f"{index:064x}",)),
        )
    assert checkpoint.disposition == "active"
    assert checkpoint.reconsideration_used is False


def test_fingerprint_excludes_volatile_receipt_metadata() -> None:
    first = run_limits.outcome_fingerprint(
        "tool",
        {"query": "x"},
        {
            "schema_version": "trace.tool-output-evidence.v1",
            "receipt_sha256": "one",
            "output": {"error": "absent"},
        },
    )
    second = run_limits.outcome_fingerprint(
        "tool",
        {"query": "x"},
        {
            "schema_version": "trace.tool-output-evidence.v1",
            "receipt_sha256": "two",
            "output": {"error": "absent"},
        },
    )
    assert first == second
    assert first != run_limits.outcome_fingerprint(
        "tool", {"query": "y"}, {"result": {"error": "present"}}
    )


def test_domain_dates_remain_meaningful_in_arguments_and_output() -> None:
    first = run_limits.outcome_fingerprint(
        "tool", {"timestamp": "Monday"}, {"timestamp": "January"}
    )
    assert first != run_limits.outcome_fingerprint(
        "tool", {"timestamp": "Tuesday"}, {"timestamp": "January"}
    )
    assert first != run_limits.outcome_fingerprint(
        "tool", {"timestamp": "Monday"}, {"timestamp": "February"}
    )


def test_repeated_output_with_new_receipt_cannot_reset_stagnation() -> None:
    checkpoint = seed_task(make_run()).checkpoint
    for receipt in range(5):
        payload: JsonObject = {
            "schema_version": "trace.tool-output-evidence.v1",
            "receipt_sha256": str(receipt),
            "output": {"error": "absent"},
        }
        evidence = run_limits.progress_evidence_fingerprint(payload)
        checkpoint = run_limits.observe_progress(
            checkpoint, run_limits.ProgressObservation("a" * 64, evidence_sha256s=(evidence,))
        )
    assert checkpoint.disposition == "blocked"


def test_new_slack_defaults_preserve_explicit_and_stored_budgets() -> None:
    policy = completion_policy.load_completion_policy({})
    assert policy.slack_budget.max_tool_calls == 32
    assert policy.slack_budget.max_cost_units == 50
    explicit = AgentBudget(max_tool_calls=8, max_cost_units=17)
    assert completion_policy.new_slack_budget(explicit, policy) == explicit
    assert run_limits.remaining_budget(make_run(), ()).tool_calls == 8


def test_operator_defaults_are_validated_and_affect_new_policy_only() -> None:
    policy = completion_policy.load_completion_policy(
        {"TRACE_MARKETING_MAX_TOOL_CALLS": "19", "TRACE_MARKETING_MAX_DECISION_CALLS": "45"}
    )
    assert policy.slack_budget.max_tool_calls == 19
    assert policy.segment_policy.max_decision_calls == 45
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        _ = completion_policy.load_completion_policy({"TRACE_MARKETING_MAX_DECISION_CALLS": "0"})


def test_restart_retains_reserved_calls_and_failure_fingerprints(tmp_path: Path) -> None:
    database = tmp_path / "limits.sqlite3"
    repository = SqliteAgentRunRepository(database)
    run = repository.create(make_run())
    projection = seed_task(run)
    checkpoint = run_limits.reserve_decision(projection.checkpoint)
    assert checkpoint is not None
    checkpoint = run_limits.observe_progress(checkpoint, run_limits.ProgressObservation("a" * 64))
    updated = repository.append_step(
        run,
        step(),
        state=AgentRunState.RUNNING,
        expected_revision=1,
        records=task_records(run, TaskProjection(projection.spec, checkpoint), NOW),
    )
    reopened = SqliteAgentRunRepository(database)
    restored = project_task(updated, reopened.records(run.tenant_id, run.run_id))
    assert restored.checkpoint.decision_calls == 1
    assert restored.checkpoint.fingerprints == ("a" * 64,)


def test_last_decision_is_available_for_assessment_but_never_exceeds_cap() -> None:
    checkpoint = seed_task(make_run()).checkpoint.model_copy(update={"decision_calls": 63})
    assert run_limits.reserve_decision(checkpoint) is None
    final = run_limits.reserve_decision(checkpoint, assessment=True)
    assert final is not None
    assert final.decision_calls == 64
    assert run_limits.reserve_decision(final) is None


def test_penultimate_decision_can_create_candidate_before_reserved_assessment() -> None:
    checkpoint = seed_task(make_run()).checkpoint.model_copy(update={"decision_calls": 62})
    actor = run_limits.reserve_decision(checkpoint)
    assert actor is not None
    assert actor.decision_calls == 63
    assessment = run_limits.reserve_decision(actor, assessment=True)
    assert assessment is not None
    assert assessment.decision_calls == 64


def test_remaining_cost_counts_canonical_receipts_without_budget_refill() -> None:
    run = make_run()
    receipt = ToolReceiptRecord(
        schema_version="trace.tool-receipt.v1",
        receipt_id="receipt",
        invocation_sha256="a" * 64,
        disposition="failed",
        actual_cost_units=17,
        output_schema_sha256="b" * 64,
        output_sha256="c" * 64,
        executor_id="executor",
        occurred_at=NOW,
    )
    record = AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id="receipt",
        run_id=run.run_id,
        kind=AgentRecordKind.RECEIPT,
        payload_schema_version=receipt.schema_version,
        payload=receipt.model_dump(mode="json"),
        payload_sha256=contract_sha256(receipt),
        occurred_at=NOW,
    )
    remaining = run_limits.remaining_budget(run, (record,))
    assert remaining.cost_units == 33
    assert remaining.tool_calls == 8
