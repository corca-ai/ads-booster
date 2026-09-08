# pyright: reportPrivateUsage=false
"""Acknowledged async work keeps reservations and resolves through one exact operation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.runtime import (
    AgentSession,
    Budget,
    DeferredToolExecution,
    EffectDisposition,
    MarketingAgentRuntime,
    MarketingRuntimeError,
    RuntimeState,
    SessionEvent,
    SqliteSessionStore,
    ToolAdmission,
    ToolReceipt,
    canonical_json_sha256,
    pending_deferred_execution,
)
from tests.marketing.test_agent_runtime import CAPABILITY, INVOCATION, NOW, _grant, _receipt

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.runtime import BoundToolInvocation
    from ads_booster.transport.json_types import JsonObject


class DeferredBackend:
    calls: int = 0

    def execute(self, invocation: BoundToolInvocation) -> DeferredToolExecution:
        self.calls += 1
        return DeferredToolExecution(invocation.call.call_id, invocation.call.digest, "job-1")


def deferred(
    tmp_path: Path,
) -> tuple[MarketingAgentRuntime, SqliteSessionStore, AgentSession, DeferredBackend]:
    runtime = MarketingAgentRuntime()
    store = SqliteSessionStore(tmp_path / "runtime.db")
    session = runtime.request_persisted_tool(
        store,
        AgentSession("session", Budget(3, 10)),
        ToolAdmission(CAPABILITY, INVOCATION, _grant()),
        now=NOW,
    )
    backend = DeferredBackend()
    return runtime, store, runtime.execute_persisted_tool(store, session, backend, now=NOW), backend


def test_restart_keeps_reservation_until_exact_terminal_result(tmp_path: Path) -> None:
    runtime, store, session, backend = deferred(tmp_path)
    assert session.state is RuntimeState.EXECUTING
    assert session.execution_started
    assert session.spent_cost_units == 0
    assert session.reserved_cost_units == 5
    restarted = SqliteSessionStore(store.database_path).load(session.session_id)
    assert restarted == session
    assert pending_deferred_execution(session) == DeferredToolExecution(
        INVOCATION.call.call_id, INVOCATION.call.digest, "job-1"
    )
    assert runtime.reconcile_interrupted_execution(store, session, now=NOW) == session
    with pytest.raises(MarketingRuntimeError, match="already_deferred"):
        _ = runtime.finish_persisted_tool_execution(store, session, backend, now=NOW)
    assert backend.calls == 1
    result = runtime.resolve_persisted_deferred(
        store, session, "job-1", _receipt(grant_sha256=_grant().digest, cost=3), now=NOW
    )
    assert result.spent_cost_units == 3
    assert result.reserved_cost_units == 0
    assert result.pending_call is None
    assert pending_deferred_execution(result) is None
    assert store.load(session.session_id) == result
    with pytest.raises(MarketingRuntimeError):
        _ = runtime.resolve_persisted_deferred(store, result, "job-1", _receipt(), now=NOW)


@pytest.mark.parametrize(
    ("operation", "receipt"),
    [
        ("wrong", _receipt(grant_sha256=_grant().digest)),
        ("job-1", _receipt(call_id="other", grant_sha256=_grant().digest)),
        ("job-1", _receipt(call_sha256="f" * 64, grant_sha256=_grant().digest)),
        ("job-1", _receipt(grant_sha256="f" * 64)),
        ("job-1", _receipt(grant_sha256=_grant().digest, cost=6)),
        ("job-1", _receipt(EffectDisposition.UNKNOWN_SIDE_EFFECT, grant_sha256=_grant().digest)),
    ],
)
def test_wrong_completion_does_not_release_budget(
    tmp_path: Path, operation: str, receipt: ToolReceipt
) -> None:
    runtime, store, session, _ = deferred(tmp_path)
    with pytest.raises(MarketingRuntimeError):
        _ = runtime.resolve_persisted_deferred(store, session, operation, receipt, now=NOW)
    assert store.load(session.session_id) == session


@pytest.mark.parametrize("mutation", ["duplicate", "rebind", "wrong_call"])
def test_reducer_rejects_duplicate_or_rebound_acknowledgement(
    tmp_path: Path, mutation: str
) -> None:
    _, store, session, _ = deferred(tmp_path)
    last = session.events[-1]
    payload = dict(last.payload)
    if mutation == "rebind":
        payload["operation_id"] = "job-2"
    elif mutation == "wrong_call":
        payload["call_sha256"] = "e" * 64
    event = SessionEvent(
        len(session.events) + 1, last.event_type, payload, canonical_json_sha256(payload), NOW
    )
    invalid = replace(session, events=(*session.events, event))
    with pytest.raises(MarketingRuntimeError, match="deferred_invalid"):
        store.save(invalid, expected_sequence=len(session.events))
    assert store.load(session.session_id) == session


def test_second_admission_denied_while_deferred(tmp_path: Path) -> None:
    runtime, store, session, _ = deferred(tmp_path)
    with pytest.raises(MarketingRuntimeError):
        _ = runtime.request_persisted_tool(
            store, session, ToolAdmission(CAPABILITY, INVOCATION, _grant()), now=NOW
        )
    assert store.load(session.session_id) == session


def test_acknowledgement_requires_persisted_execution_start(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = SqliteSessionStore(tmp_path / "runtime.db")
    session = runtime.request_persisted_tool(
        store,
        AgentSession("session", Budget(3, 10)),
        ToolAdmission(CAPABILITY, INVOCATION, _grant()),
        now=NOW,
    )
    payload: JsonObject = {
        "call_id": INVOCATION.call.call_id,
        "call_sha256": INVOCATION.call.digest,
        "operation_id": "job-1",
    }
    event = SessionEvent(
        len(session.events) + 1,
        "tool_execution_deferred",
        payload,
        canonical_json_sha256(payload),
        NOW,
    )
    with pytest.raises(MarketingRuntimeError, match="deferred_invalid"):
        store.save(
            replace(session, events=(*session.events, event)), expected_sequence=len(session.events)
        )


def test_explicit_uncertainty_preserves_budget_until_late_terminal_receipt(tmp_path: Path) -> None:
    runtime, store, session, backend = deferred(tmp_path)
    with pytest.raises(MarketingRuntimeError, match="deferred_uncertainty_invalid"):
        _ = runtime.mark_persisted_deferred_uncertain(store, session, "wrong-job", now=NOW)
    assert store.load(session.session_id) == session
    uncertain = runtime.mark_persisted_deferred_uncertain(store, session, "job-1", now=NOW)
    assert uncertain.state is RuntimeState.AWAITING_RECONCILIATION
    assert uncertain.reserved_cost_units == 5
    assert uncertain.spent_cost_units == 0
    assert uncertain.pending_call == session.pending_call
    restarted = SqliteSessionStore(store.database_path).load(session.session_id)
    assert restarted == uncertain
    assert pending_deferred_execution(uncertain) == pending_deferred_execution(session)
    with pytest.raises(MarketingRuntimeError, match="deferred_uncertainty_invalid"):
        _ = runtime.mark_persisted_deferred_uncertain(store, uncertain, "job-1", now=NOW)
    with pytest.raises(MarketingRuntimeError, match="deferred_receipt_invalid"):
        _ = runtime.resolve_persisted_deferred(
            store, uncertain, "job-1", _receipt(grant_sha256=_grant().digest), now=NOW
        )
    with pytest.raises(MarketingRuntimeError, match="receipt_call_id_mismatch"):
        _ = runtime.resolve_persisted_reconciliation(
            store, uncertain, _receipt(call_id="other", grant_sha256=_grant().digest), now=NOW
        )
    completed = runtime.resolve_persisted_reconciliation(
        store, uncertain, _receipt(grant_sha256=_grant().digest, cost=2), now=NOW
    )
    assert completed.spent_cost_units == 2
    assert completed.reserved_cost_units == 0
    assert completed.pending_call is None
    assert store.load(session.session_id) == completed
    assert backend.calls == 1


@pytest.mark.parametrize("mutation", ["wrong-operation", "duplicate"])
def test_reducer_rejects_invalid_uncertainty_event(tmp_path: Path, mutation: str) -> None:
    runtime, store, session, _ = deferred(tmp_path)
    uncertain = runtime.mark_persisted_deferred_uncertain(store, session, "job-1", now=NOW)
    if mutation == "wrong-operation":
        event = uncertain.events[-1]
        payload = {**event.payload, "operation_id": "wrong-job"}
        bad = replace(event, payload=payload, payload_sha256=canonical_json_sha256(payload))
        invalid = replace(uncertain, events=(*uncertain.events[:-1], bad))
    else:
        event = replace(uncertain.events[-1], sequence=len(uncertain.events) + 1)
        invalid = replace(uncertain, events=(*uncertain.events, event))
    with pytest.raises(MarketingRuntimeError, match="deferred_uncertainty_invalid"):
        _ = pending_deferred_execution(invalid)
