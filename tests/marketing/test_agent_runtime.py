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
    ToolCall,
    ToolCapability,
    ToolReceipt,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CAPABILITY = ToolCapability("publish.threads", "a" * 64, "external", 5)
CALL = ToolCall("call-1", "campaign:1:publish", "publish.threads", "a" * 64, "b" * 64, "external")


def test_external_tool_waits_for_exact_unrevoked_grant() -> None:
    runtime = MarketingAgentRuntime()
    waiting = runtime.request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW
    )
    assert waiting.state is RuntimeState.AWAITING_HUMAN
    assert waiting.tool_calls == 0

    grant = ApprovalGrant(CALL.digest, "reviewer", NOW + timedelta(minutes=1))
    dispatched = runtime.request_tool(
        waiting, CAPABILITY, CALL, now=NOW, grant=grant
    )
    assert dispatched.state is RuntimeState.EXECUTING
    assert dispatched.events[-1].event_type == "tool_dispatched"


def test_unknown_external_effect_requires_reconciliation_not_retry() -> None:
    runtime = MarketingAgentRuntime()
    grant = ApprovalGrant(CALL.digest, "reviewer", NOW + timedelta(minutes=1))
    dispatched = runtime.request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    reconciled = runtime.record_receipt(
        dispatched,
        ToolReceipt(CALL.call_id, EffectDisposition.UNKNOWN_SIDE_EFFECT, 5, "c" * 64),
        now=NOW,
    )
    assert reconciled.state is RuntimeState.AWAITING_RECONCILIATION


def test_budget_is_reserved_before_dispatch() -> None:
    stopped = MarketingAgentRuntime().request_tool(
        AgentSession("session-1", Budget(1, 4)), CAPABILITY, CALL, now=NOW,
        grant=ApprovalGrant(CALL.digest, "reviewer", NOW + timedelta(minutes=1)),
    )
    assert stopped.state is RuntimeState.STOPPED
    assert stopped.events[-1].event_type == "budget_cost_exhausted"


def test_capability_id_alone_cannot_authorize_tool_call() -> None:
    wrong = ToolCall(
        "call-1", "campaign:1:publish", "publish.threads", "d" * 64, "b" * 64, "external"
    )
    with pytest.raises(MarketingRuntimeError, match="capability_mismatch"):
        _ = MarketingAgentRuntime().request_tool(
            AgentSession("session-1", Budget(2, 10)), CAPABILITY, wrong, now=NOW
        )


def test_serialized_session_reopens_with_exact_event_history_and_cas(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    grant = ApprovalGrant(CALL.digest, "reviewer", NOW + timedelta(minutes=1))
    dispatched = runtime.request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, CALL, now=NOW, grant=grant
    )
    store = JsonSessionStore(tmp_path)
    store.save(dispatched, expected_sequence=0)

    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened == dispatched
    with pytest.raises(MarketingRuntimeError, match="compare_and_swap"):
        store.save(dispatched, expected_sequence=0)
