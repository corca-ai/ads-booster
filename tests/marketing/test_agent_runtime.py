# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.marketing.runtime import (
    AgentSession,
    ApprovalGrant,
    BoundToolInvocation,
    Budget,
    EffectDisposition,
    JsonSessionStore,
    MarketingAgentRuntime,
    MarketingRuntimeError,
    RuntimeState,
    SessionEvent,
    ToolAdmission,
    ToolCapability,
    ToolReceipt,
    bind_tool_invocation,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CAPABILITY = ToolCapability("publish.threads", "a" * 64, "b" * 64, "external", 5)
INVOCATION = bind_tool_invocation(
    CAPABILITY,
    call_id="call-1",
    idempotency_key="campaign:1:publish",
    request={"campaign_id": "campaign-1", "operation": "publish"},
)
CALL = INVOCATION.call


class RecordingBackend:
    def __init__(self, receipt: ToolReceipt) -> None:
        self.receipt: ToolReceipt = receipt
        self.calls: list[BoundToolInvocation] = []

    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt:
        self.calls.append(invocation)
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
    return ToolAdmission(CAPABILITY, INVOCATION, grant)


def test_public_runtime_surface_has_no_non_durable_tool_dispatch() -> None:
    runtime = MarketingAgentRuntime()

    assert not hasattr(runtime, "request_tool")
    assert not hasattr(runtime, "execute_tool")


def test_external_tool_waits_for_exact_unrevoked_grant() -> None:
    runtime = MarketingAgentRuntime()
    waiting = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW
    )
    assert waiting.state is RuntimeState.AWAITING_HUMAN
    assert waiting.tool_calls == 0

    grant = _grant()
    dispatched = runtime._request_tool(waiting, CAPABILITY, INVOCATION, now=NOW, grant=grant)
    assert dispatched.state is RuntimeState.EXECUTING
    assert dispatched.events[-1].event_type == "tool_dispatched"


def test_unknown_external_effect_requires_reconciliation_not_retry() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
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
        INVOCATION,
        now=NOW,
        grant=_grant(),
    )
    assert stopped.state is RuntimeState.STOPPED
    assert stopped.events[-1].event_type == "budget_cost_exhausted"


def test_capability_id_alone_cannot_authorize_tool_call() -> None:
    wrong = BoundToolInvocation(replace(CALL, descriptor_sha256="d" * 64), INVOCATION.request_json)
    with pytest.raises(MarketingRuntimeError, match="capability_mismatch"):
        _ = MarketingAgentRuntime()._request_tool(
            AgentSession("session-1", Budget(2, 10)), CAPABILITY, wrong, now=NOW
        )


def test_serialized_session_reopens_with_exact_event_history_and_cas(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
    )
    store = JsonSessionStore(tmp_path)
    store.save(dispatched, expected_sequence=0)

    reopened = JsonSessionStore(tmp_path).load("session-1")
    assert reopened == dispatched
    with pytest.raises(MarketingRuntimeError, match="compare_and_swap"):
        store.save(dispatched, expected_sequence=0)


def test_bound_invocation_uses_one_unicode_safe_canonical_request_digest() -> None:
    first = bind_tool_invocation(
        CAPABILITY,
        call_id="call-unicode",
        idempotency_key="campaign:unicode:publish",
        request={"operation": "publish", "message": "잠금화면 캐릭터"},
    )
    second = bind_tool_invocation(
        CAPABILITY,
        call_id="call-unicode",
        idempotency_key="campaign:unicode:publish",
        request={"message": "잠금화면 캐릭터", "operation": "publish"},
    )

    assert first.request_json == second.request_json
    assert first.call.input_sha256 == second.call.input_sha256
    assert first.call.digest == second.call.digest


def test_dispatch_persists_the_exact_bound_invocation(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    _ = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )

    reopened = store.load("session-1")

    assert reopened is not None
    assert reopened.pending_invocation == INVOCATION
    assert reopened.events[-1].payload == {
        "invocation": {
            "schema_version": "trace.bound-tool-invocation.v1",
            "call": {
                "call_id": CALL.call_id,
                "idempotency_key": CALL.idempotency_key,
                "capability_id": CALL.capability_id,
                "descriptor_sha256": CALL.descriptor_sha256,
                "request_schema_sha256": CALL.request_schema_sha256,
                "input_sha256": CALL.input_sha256,
                "effect_class": CALL.effect_class,
            },
            "request": {"campaign_id": "campaign-1", "operation": "publish"},
        },
        "approval_grant_sha256": grant.digest,
        "reserved_cost_units": CAPABILITY.worst_case_cost_units,
    }


def test_session_store_rejects_rewritten_committed_history(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
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
        pending_invocation=dispatched.pending_invocation,
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
            *dispatched.events[1:],
        ),
    )

    with pytest.raises(MarketingRuntimeError, match="event_history_mismatch"):
        store.save(rewritten, expected_sequence=len(dispatched.events))


def test_invalid_bound_request_never_reaches_a_backend() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
    )
    forged = BoundToolInvocation(CALL, '{"campaign_id":"campaign-1","operation":"delete"}')
    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))

    reconciled = runtime._execute_tool(
        replace(dispatched, pending_invocation=forged), backend, now=NOW
    )

    assert backend.calls == []
    assert reconciled.state is RuntimeState.AWAITING_RECONCILIATION
    assert reconciled.events[-1].event_type == "tool_invocation_rejected"


def test_store_rejects_legacy_pending_call_without_its_invocation(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )
    with pytest.raises(MarketingRuntimeError, match="pending_invocation_checkpoint_mismatch"):
        store.save(
            replace(dispatched, pending_invocation=None),
            expected_sequence=len(dispatched.events),
        )


def test_store_rejects_pending_checkpoint_for_a_different_valid_invocation(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    replacement = bind_tool_invocation(
        CAPABILITY,
        call_id="call-replacement",
        idempotency_key="campaign:replacement:publish",
        request={"campaign_id": "campaign-replacement", "operation": "publish"},
    )
    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))

    with pytest.raises(MarketingRuntimeError, match="pending_dispatch_checkpoint_mismatch"):
        store.save(
            replace(
                dispatched,
                pending_call=replacement.call,
                pending_invocation=replacement,
            ),
            expected_sequence=len(dispatched.events),
        )

    assert backend.calls == []


def test_store_rejects_pending_grant_that_differs_from_the_dispatch_event(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="pending_grant_checkpoint_mismatch"):
        store.save(
            replace(dispatched, pending_grant_sha256=None),
            expected_sequence=len(dispatched.events),
        )


def test_store_rejects_pending_budget_that_differs_from_the_dispatch_event(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="pending_budget_checkpoint_mismatch"):
        store.save(
            replace(dispatched, reserved_cost_units=0),
            expected_sequence=len(dispatched.events),
        )


def test_event_ledger_reconstructs_completed_authority_and_budget_checkpoint(
    tmp_path: Path,
) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    completed = runtime.execute_persisted_tool(
        store,
        dispatched,
        RecordingBackend(_receipt(grant_sha256=grant.digest)),
        now=NOW,
    )
    tampered_checkpoints = (
        (replace(completed, state=RuntimeState.CREATED), "state_checkpoint_mismatch"),
        (replace(completed, spent_cost_units=0), "spent_budget_checkpoint_mismatch"),
        (replace(completed, tool_calls=0), "tool_calls_checkpoint_mismatch"),
        (replace(completed, dispatched_idempotency_keys=()), "idempotency_checkpoint_mismatch"),
        (replace(completed, consumed_grant_sha256s=()), "grant_consumption_checkpoint_mismatch"),
    )

    for tampered, error in tampered_checkpoints:
        with pytest.raises(MarketingRuntimeError, match=error):
            store.save(tampered, expected_sequence=len(completed.events))


def test_event_ledger_header_freezes_the_session_budget(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )

    assert dispatched.events[0].event_type == "session_started"
    with pytest.raises(MarketingRuntimeError, match="budget_checkpoint_mismatch"):
        store.save(
            replace(dispatched, budget=Budget(20, 100)),
            expected_sequence=len(dispatched.events),
        )


def test_event_ledger_rejects_invalid_runtime_event_and_payload_digest(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=_grant()
    )
    invalid_runtime_event = runtime._append(
        dispatched,
        RuntimeState.EXECUTING,
        "tool_unregistered_effect",
        {},
        NOW,
    )
    invalid_payload_digest = replace(
        dispatched,
        events=(replace(dispatched.events[0], payload_sha256="f" * 64), *dispatched.events[1:]),
    )

    with pytest.raises(MarketingRuntimeError, match="event_transition_invalid"):
        JsonSessionStore(tmp_path / "runtime-event").save(
            invalid_runtime_event,
            expected_sequence=0,
        )
    with pytest.raises(MarketingRuntimeError, match="event_payload_digest_mismatch"):
        JsonSessionStore(tmp_path / "payload-digest").save(
            invalid_payload_digest,
            expected_sequence=0,
        )


def test_event_ledger_rejects_any_event_after_finalization(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    terminal = runtime.finalize_persisted_session(
        store,
        AgentSession("session-1", Budget(1, 1)),
        state=RuntimeState.COMPLETED,
        reason="evaluation complete",
        now=NOW,
    )
    post_final_event = SessionEvent(
        len(terminal.events) + 1,
        "feature_after_finalization",
        {},
        sha256(b"{}").hexdigest(),
        NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="event_after_finalization"):
        JsonSessionStore(tmp_path / "post-final").save(
            replace(terminal, events=(*terminal.events, post_final_event)),
            expected_sequence=0,
        )
    with pytest.raises(MarketingRuntimeError, match="session_already_finalized"):
        _ = runtime.finalize_persisted_session(
            store,
            terminal,
            state=RuntimeState.COMPLETED,
            reason="duplicate",
            now=NOW,
        )


def test_event_ledger_rejects_receipt_without_a_durable_execution_start(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    receipt_without_start = runtime.record_receipt(
        dispatched,
        _receipt(grant_sha256=grant.digest),
        now=NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="event_receipt_invalid"):
        store.save(receipt_without_start, expected_sequence=len(dispatched.events))


def test_event_ledger_rejects_duplicate_execution_start_and_sequence_hole(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )
    started = runtime.start_persisted_tool_execution(store, dispatched, now=NOW)
    duplicate_start = runtime._append(
        started,
        RuntimeState.EXECUTING,
        "tool_execution_started",
        {"reference_sha256": CALL.digest},
        NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="event_transition_invalid"):
        store.save(duplicate_start, expected_sequence=len(started.events))
    with pytest.raises(MarketingRuntimeError, match="event_sequence_mismatch"):
        JsonSessionStore(tmp_path / "sequence-hole").save(
            replace(dispatched, events=(replace(dispatched.events[0], sequence=2),)),
            expected_sequence=0,
        )


def test_reconciliation_cannot_be_finalized_without_a_resolution(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(grant),
        now=NOW,
    )
    reconciled = runtime.execute_persisted_tool(
        store,
        dispatched,
        RecordingBackend(
            _receipt(EffectDisposition.UNKNOWN_SIDE_EFFECT, grant_sha256=grant.digest)
        ),
        now=NOW,
    )

    with pytest.raises(MarketingRuntimeError, match="finalization_requires_reconciliation"):
        _ = runtime.finalize_persisted_session(
            store,
            reconciled,
            state=RuntimeState.COMPLETED,
            reason="must not bypass unknown effect",
            now=NOW,
        )


def test_store_rejects_checkpoint_rewrite_that_could_redeliver_an_effect(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    dispatched = runtime.request_persisted_tool(
        store,
        AgentSession("session-1", Budget(2, 10)),
        _admission(_grant()),
        now=NOW,
    )
    claimed = runtime.start_persisted_tool_execution(store, dispatched, now=NOW)
    with pytest.raises(MarketingRuntimeError, match="execution_checkpoint_mismatch"):
        store.save(
            replace(claimed, execution_started=False),
            expected_sequence=len(claimed.events),
        )


def test_legacy_unicode_terminal_session_is_read_only_not_reexecuted(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    terminal = runtime.finalize_persisted_session(
        store,
        AgentSession("session-1", Budget(1, 1)),
        state=RuntimeState.COMPLETED,
        reason="잠금화면 검증 완료",
        now=NOW,
    )
    path = tmp_path / "session-1.json"
    legacy = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    _ = legacy.pop("serialization_version")
    legacy_events = cast("list[dict[str, object]]", legacy["events"])
    for event in legacy_events:
        payload = cast("dict[str, object]", event["payload"])
        event["payload_sha256"] = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    legacy_without_digest = {key: value for key, value in legacy.items() if key != "session_sha256"}
    legacy["session_sha256"] = sha256(
        json.dumps(legacy_without_digest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _ = path.write_text(json.dumps(legacy), encoding="utf-8")

    reopened = store.load("session-1")

    assert reopened is not None
    assert reopened.state is RuntimeState.COMPLETED
    with pytest.raises(MarketingRuntimeError, match="legacy_session_read_only"):
        store.save(reopened, expected_sequence=len(terminal.events))


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
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
    )
    backend = RecordingBackend(_receipt(grant_sha256=grant.digest))

    completed = runtime._execute_tool(dispatched, backend, now=NOW)

    assert backend.calls == [INVOCATION]
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

    assert backend.calls == [INVOCATION]
    assert completed.pending_call is None
    assert store.load("session-1") == completed


def test_durable_driver_rejects_an_uncommitted_or_already_started_effect(tmp_path: Path) -> None:
    runtime = MarketingAgentRuntime()
    store = JsonSessionStore(tmp_path)
    grant = _grant()
    uncommitted = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
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
        _ = runtime._request_tool(
            reconciled, CAPABILITY, INVOCATION, now=NOW, grant=_grant("grant-2")
        )


def test_tampered_receipt_enters_reconciliation_without_accepting_it() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
    )
    backend = RecordingBackend(_receipt(call_sha256="d" * 64, grant_sha256=grant.digest))

    result = runtime._execute_tool(dispatched, backend, now=NOW)

    assert backend.calls == [INVOCATION]
    assert result.state is RuntimeState.AWAITING_RECONCILIATION
    assert result.pending_call == CALL
    assert result.spent_cost_units == 0


def test_duplicate_idempotency_grant_and_terminal_dispatch_are_rejected() -> None:
    runtime = MarketingAgentRuntime()
    grant = _grant()
    dispatched = runtime._request_tool(
        AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
    )
    completed = runtime.record_receipt(dispatched, _receipt(grant_sha256=grant.digest), now=NOW)
    with pytest.raises(MarketingRuntimeError, match="tool_approval_grant_invalid"):
        _ = runtime._request_tool(completed, CAPABILITY, INVOCATION, now=NOW, grant=grant)
    with pytest.raises(MarketingRuntimeError, match="duplicate_tool_idempotency"):
        _ = runtime._request_tool(
            completed, CAPABILITY, INVOCATION, now=NOW, grant=_grant("grant-2")
        )
    with pytest.raises(MarketingRuntimeError, match="after_terminal"):
        _ = runtime._request_tool(
            AgentSession("session-3", Budget(2, 10), state=RuntimeState.STOPPED),
            CAPABILITY,
            INVOCATION,
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
            AgentSession("session-1", Budget(2, 10)), CAPABILITY, INVOCATION, now=NOW, grant=grant
        )
