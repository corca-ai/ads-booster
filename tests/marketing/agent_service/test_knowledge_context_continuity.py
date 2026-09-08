# pyright: reportPrivateUsage=false
"""Merged knowledge snapshots remain canonical history, not unfiltered recall."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, AgentStepKind
from ads_booster.marketing.agent_service.application import _record, _step
from ads_booster.marketing.agent_service.work_continuation import continue_work
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    InvokeThenStopReasoning,
    _request,
    _service,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.agent_service.knowledge import KnowledgeServiceAdapter


def test_previous_knowledge_projection_is_not_reused_as_current_evidence(tmp_path: Path) -> None:
    reasoning = AskThenStopReasoning()
    service = _service(tmp_path / "state.db", reasoning)
    run = service.create(_request(), now=NOW)
    record = _record(
        run,
        record_id="previous-knowledge",
        kind=AgentRecordKind.EVIDENCE,
        payload={
            "schema_version": "trace.prepared-knowledge-context-record.v1",
            "prepared_context": {"previously_visible_text": "revoked knowledge"},
        },
        now=NOW,
    )
    _ = service.repository.append_step(
        run,
        _step(
            run,
            kind=AgentStepKind.OBSERVE,
            input_sha256=record.payload_sha256,
            output_sha256=record.payload_sha256,
            now=NOW,
        ),
        state=AgentRunState.AWAITING_INPUT,
        expected_revision=run.revision,
        records=(record,),
    )
    _ = service.submit_input("trace", run.run_id, {"question": "Continue"}, now=NOW)
    assert all(
        item.get("schema_version") != record.payload_schema_version
        for item in reasoning.requests[-1].evidence
    )
    assert any(
        item.record_id == record.record_id
        for item in service.repository.records("trace", run.run_id)
    )


def test_stale_knowledge_does_not_replan_over_admitted_runtime_call(tmp_path: Path) -> None:

    service = _service(tmp_path / "state.db", InvokeThenStopReasoning())

    def crash(point: str) -> None:
        if point == "runtime_admitted":
            message = "fixture_crash"
            raise RuntimeError(message)

    service.fault_hook = crash
    with pytest.raises(RuntimeError, match="fixture_crash"):
        _ = service.create(_request(), now=NOW)
    before = service.runtime_store.load("run-one")
    assert before is not None
    assert before.pending_invocation is not None
    assert not before.execution_started
    # No prepared knowledge exists for the newly configured owner. Its preparation
    # must not run while the previous runtime admission still owns the call.
    service.knowledge = cast("KnowledgeServiceAdapter", object())
    service.fault_hook = None
    result = service.drive("trace", "run-one", now=NOW)
    assert result.state is AgentRunState.AWAITING_RECONCILIATION
    assert service.runtime_store.load("run-one") == before


def test_knowledge_prepare_uses_current_canonical_followup_query(tmp_path: Path) -> None:

    service = _service(tmp_path / "state.db", AskThenStopReasoning())
    run = service.create(_request(), now=NOW)
    queries: list[object] = []

    class Probe:
        def filter_snapshot(self, run_id: str, snapshot: object) -> object:
            _ = run_id
            return snapshot

        def prepare(self, *args: object, **kwargs: object) -> object:
            _ = args
            queries.append(kwargs.get("query"))
            message = "preparation_probe"
            raise RuntimeError(message)

    service.knowledge = cast("KnowledgeServiceAdapter", cast("object", Probe()))
    with pytest.raises(RuntimeError, match="preparation_probe"):
        _ = continue_work(
            service,
            "trace",
            run.run_id,
            event_id="query-followup",
            actor_id="member",
            note="학생 타깃으로 바꿔줘",
            action="revise",
            now=NOW,
        )
    assert queries == [run.goal.objective + "\n학생 타깃으로 바꿔줘"]


def test_current_knowledge_check_is_bound_to_existing_canonical_tenant(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.db", AskThenStopReasoning())
    run = service.create(_request(), now=NOW)
    assert service.knowledge_is_current("trace", run.run_id)
    assert not service.knowledge_is_current("other", run.run_id)
    service.knowledge = cast("KnowledgeServiceAdapter", object())
    assert not service.knowledge_is_current("trace", run.run_id)
