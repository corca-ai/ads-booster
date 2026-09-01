# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.marketing.runtime import (
    AgentSession,
    ApprovalGrant,
    Budget,
    EffectDisposition,
    JsonSessionStore,
    MarketingAgentRuntime,
    MarketingRuntimeError,
    RuntimeState,
    SessionEvent,
    ToolAdmission,
    ToolCall,
    ToolCapability,
    ToolReceipt,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CAPABILITY = ToolCapability("publish.threads", "a" * 64, "external", 5)
CALL = ToolCall("call-1", "campaign:1:publish", "publish.threads", "a" * 64, "b" * 64, "external")


class RecordingBackend:
    def __init__(self, receipt: ToolReceipt) -> None:
        self.receipt: ToolReceipt = receipt
        self.calls: list[ToolCall] = []

    def execute(self, call: ToolCall) -> ToolReceipt:
        self.calls.append(call)
        return self.receipt


def _grant(grant_id: str = "grant-1") -> ApprovalGrant:
    return ApprovalGrant(grant_id, CALL.digest, "reviewer", NOW + timedelta(minutes=1))


def _receipt(
    disposition: EffectDisposition = EffectDisposition.SUCCEEDED,
    *,
    call_id: str = CALL.call_id,
    call_sha256: str = CALL.digest,
    grant_sha256: str | None = None,
    cost: int = 5,
) -> ToolReceipt:
    return ToolReceipt(call_id, call_sha256, grant_sha256, disposition, cost, "c" * 64)


def _admission(grant: ApprovalGrant) -> ToolAdmission:
    return ToolAdmission(CAPABILITY, CALL, grant)


def test_public_runtime_surface_has_no_non_durable_tool_dispatch() -> None:
    runtime = MarketingAgentRuntime()

    assert not hasattr(runtime, "request_tool")
    assert not hasattr(runtime, "execute_tool")


def test_external_tool_waits_for_exact_unrevoked_grant() -> None:
    runtime = MarketingAgentRuntime()
    waiting = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW
    )
    assert waiting.state is RuntimeState.AWAITING_HUMAN
    assert waiting.tool_calls == 0

    grant = _grant()
    dispatched = runtime._request_tool(waiting, CAPABILITY, CALL, now=NOW, grant=grant)
    assert dispatched.state is RuntimeState.EXECUTING
    assert dispatched.events[-1].event_type == "tool_dispatched"


def test_unknown_external_effect_requires_reconciliation_not_retry() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    reconciled = runtime.record_receipt(
        dispatched,
        _receipt(EffectDisposition.UNKNOWN_SIDE_EFFECT, grant_sha256=grant.digest),
        now=NOW,
    )
    assert reconciled.state is RuntimeState.AWAITING_RECONCILIATION


def test_budget_is_reserved_before_dispatch() -> None:
    stopped = MarketingAgentRuntime()._request_tool(
        AgentSession("session-1", Budget(1, 4)),
        CAPABILITY,
        CALL,
        now=NOW,
        grant=_grant(),
    )
    assert stopped.state is RuntimeState.STOPPED
    assert stopped.events[-1].event_type == "budget_cost_exhausted"


def test_capability_id_alone_cannot_authorize_tool_call() -> None:
    wrong = ToolCall(
        "call-1", "campaign:1:publish", "publish.threads", "d" * 64, "b" * 64, "external"
    )
    with pytest.raises(MarketingRuntimeError, match="capability_mismatch"):
        _ = MarketingAgentRuntime()._request_tool(
            AgentSession("session-1", Budget(2, 10)), CAPABILITY, wrong, now=NOW
        )


def test_serialized_session_reopens_with_exact_event_history_and_cas(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    store = JsonSessionStore(tmp_path)
    store.save(dispatched, expected_sequence=0)

    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened == dispatched
    with pytest.raises(MarketingRuntimeError, match="compare_and_swap"):
        store.save(dispatched, expected_sequence=0)


def test_session_store_rejects_rewritten_committed_history(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    store = JsonSessionStore(tmp_path)
    store.save(dispatched, expected_sequence=0)
    rewritten = AgentSession(
        session_id=dispatched.session_id,
        budget=dispatched.budget,
        state=dispatched.state,
        spent_cost_units=dispatched.spent_cost_units,
        reserved_cost_units=dispatched.reserved_cost_units,
        tool_calls=dispatched.tool_calls,
        pending_call=dispatched.pending_call,
        pending_grant_sha256=dispatched.pending_grant_sha256,
        dispatched_idempotency_keys=dispatched.dispatched_idempotency_keys,
        consumed_grant_sha256s=dispatched.consumed_grant_sha256s,
        events=(
            SessionEvent(
                1,
                "rewritten",
                dispatched.events[0].payload,
                dispatched.events[0].payload_sha256,
                NOW,
            ),
        ),
    )

    with pytest.raises(MarketingRuntimeError, match="event_history_mismatch"):
        store.save(rewritten, expected_sequence=1)


def test_persisted_workflow_events_replay_payload_and_cannot_forge_tool_receipts(
    tmp_path: Path,
) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    recorded = runtime.append_persisted_event(
        store,
        AgentSession("session-1", Budget(1, 1)),
        event_type="feature_goal_committed",
        payload={"goal_id": "goal-1", "iteration": 1},
        now=NOW,
    )
    assert recorded.events[-1].event_type == "feature_goal_committed"

    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened is not None
    assert reopened.events[-1].payload == {"goal_id": "goal-1", "iteration": 1}
    with pytest.raises(MarketingRuntimeError, match="runtime_reserved"):
        _ = runtime.append_persisted_event(
            store,
            reopened,
            event_type="tool_succeeded",
            payload={"receipt_sha256": "c" * 64},
            now=NOW,
        )


def test_backend_runs_once_then_receipt_releases_the_pending_call() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))

    completed = runtime._execute_tool(dispatched, backend, now=NOW)

    assert backend.calls == [CALL]
    assert completed.pending_call is None
    assert completed.spent_cost_units == 5
    assert completed.reserved_cost_units == 0
    with pytest.raises(MarketingRuntimeError, match="without_pending"):
        _ = runtime._execute_tool(completed, backend, now=NOW)


def test_durable_driver_commits_dispatch_before_calling_backend(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    assert store.load("session-1") == dispatched

    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))
    completed = runtime.execute_persisted_tool(store, dispatched, backend, now=NOW)

    assert backend.calls == [CALL]
    assert completed.pending_call is None
    assert store.load("session-1") == completed


def test_durable_driver_rejects_an_uncommitted_or_already_started_effect(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    uncommitted = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))
    with pytest.raises(MarketingRuntimeError, match="not_currently_persisted"):
        _ = runtime.execute_persisted_tool(store, uncommitted, backend, now=NOW)
    assert backend.calls == []

    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    claimed = runtime.start_persisted_tool_execution(store, dispatched, now=NOW)
    with pytest.raises(MarketingRuntimeError, match="already_started"):
        _ = runtime.execute_persisted_tool(store, claimed, backend, now=NOW)
    assert backend.calls == []


def test_restart_after_effect_claim_requires_reconciliation_not_redelivery(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    _ = runtime.start_persisted_tool_execution(store, dispatched, now=NOW)
    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened is not None

    reconciled = runtime.reconcile_interrupted_execution(store, reopened, now=NOW)

    assert reconciled.state is RuntimeState.AWAITING_RECONCILIATION
    assert reconciled.pending_call == CALL
    with pytest.raises(MarketingRuntimeError, match="requires_reconciliation"):
        _ = runtime._request_tool(reconciled, CAPABILITY, CALL, now=NOW, grant=_grant("grant-2"))


def test_tampered_receipt_enters_reconciliation_without_accepting_it() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    backend = RecordingBackend(_receipt(call_sha256="d" * 64, grant_sha256=grant.digest))

    result = runtime._execute_tool(dispatched, backend, now=NOW)

    assert backend.calls == [CALL]
    assert result.state is RuntimeState.AWAITING_RECONCILIATION
    assert result.pending_call == CALL
    assert result.spent_cost_units == 0


def test_duplicate_idempotency_grant_and_terminal_dispatch_are_rejected() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    completed = runtime.record_receipt(dispatched, _receipt(grant_sha256=grant.digest), now=NOW)
    with pytest.raises(MarketingRuntimeError, match="tool_approval_grant_invalid"):
        _ = runtime._request_tool(completed, CAPABILITY, CALL, now=NOW, grant=grant)
    with pytest.raises(MarketingRuntimeError, match="duplicate_tool_idempotency"):
        _ = runtime._request_tool(completed, CAPABILITY, CALL, now=NOW, grant=_grant("grant-2"))
    with pytest.raises(MarketingRuntimeError, match="after_terminal"):
        _ = runtime._request_tool(
            AgentSession("session-3", Budget(2, 10), state=RuntimeState.STOPPED),
            CAPABILITY,
            CALL,
            now=NOW,
            grant=_grant(),
        )


@pytest.mark.parametrize(
    "grant",
    [
        ApprovalGrant("expired", CALL.digest, "reviewer", NOW - timedelta(seconds=1)),
        ApprovalGrant("revoked", CALL.digest, "reviewer", NOW + timedelta(minutes=1), revoked=True),
    ],
)
def test_expired_or_revoked_grant_is_rejected(grant: ApprovalGrant) -> None:
    with pytest.raises(MarketingRuntimeError, match="tool_approval_grant_invalid"):
        _ = MarketingAgentRuntime()._request_tool(
            AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
        )
