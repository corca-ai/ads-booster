"""Provider-neutral, receipt-grounded primitives for the marketing agent runtime.

This module intentionally has no Cloudflare, Appium, Threads, or model-provider import.  It is the
small durable harness boundary that those integrations will later implement as tools.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, Self, cast

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import TextIO


class RuntimeState(StrEnum):
    CREATED = "created"
    EXECUTING = "executing"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    STOPPED = "stopped"
    INCONCLUSIVE = "inconclusive"
    COMPLETED = "completed"


class EffectDisposition(StrEnum):
    NO_EFFECT = "no_effect"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class Budget:
    max_tool_calls: int
    max_cost_units: int


@dataclass(frozen=True, slots=True)
class ToolCapability:
    capability_id: str
    descriptor_sha256: str
    effect_class: str
    worst_case_cost_units: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    idempotency_key: str
    capability_id: str
    descriptor_sha256: str
    input_sha256: str
    effect_class: str

    @property
    def digest(self) -> str:
        parts = (
            self.call_id,
            self.idempotency_key,
            self.capability_id,
            self.descriptor_sha256,
            self.input_sha256,
            self.effect_class,
        )
        return _digest("|".join(parts))


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    grant_id: str
    call_sha256: str
    approver_id: str
    expires_at: datetime
    remaining_uses: int = 1
    revoked: bool = False

    def allows(self, call: ToolCall, now: datetime) -> bool:
        return (
            not self.revoked
            and self.remaining_uses > 0
            and self.call_sha256 == call.digest
            and now <= self.expires_at
        )

    @property
    def digest(self) -> str:
        return _digest(
            "|".join(
                (
                    self.grant_id,
                    self.call_sha256,
                    self.approver_id,
                    self.expires_at.isoformat(),
                    str(self.remaining_uses),
                    str(self.revoked),
                )
            )
        )


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    call_id: str
    call_sha256: str
    approval_grant_sha256: str | None
    disposition: EffectDisposition
    actual_cost_units: int
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ToolAdmission:
    capability: ToolCapability
    call: ToolCall
    grant: ApprovalGrant | None = None


@dataclass(frozen=True, slots=True)
class SessionEvent:
    sequence: int
    event_type: str
    payload: JsonObject
    payload_sha256: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AgentSession:
    session_id: str
    budget: Budget
    state: RuntimeState = RuntimeState.CREATED
    spent_cost_units: int = 0
    reserved_cost_units: int = 0
    tool_calls: int = 0
    pending_call: ToolCall | None = None
    pending_grant_sha256: str | None = None
    execution_started: bool = False
    dispatched_idempotency_keys: tuple[str, ...] = ()
    consumed_grant_sha256s: tuple[str, ...] = ()
    events: tuple[SessionEvent, ...] = ()


class ToolBackend(Protocol):
    def execute(self, call: ToolCall) -> ToolReceipt: ...


class SessionStore(Protocol):
    def load(self, session_id: str) -> AgentSession | None: ...

    def save(self, session: AgentSession, *, expected_sequence: int) -> None: ...


class MarketingRuntimeError(ValueError):
    """A deterministic harness admission or receipt error."""


def tool_receipt_from_event(event: SessionEvent) -> ToolReceipt:
    """Read a receipt only from the runtime-reserved event that persisted it."""
    if event.event_type not in {f"tool_{item}" for item in EffectDisposition}:
        raise MarketingRuntimeError("event_does_not_contain_tool_receipt")
    payload = _as_object(event.payload)
    return ToolReceipt(
        call_id=_string(payload, "call_id"),
        call_sha256=_string(payload, "call_sha256"),
        approval_grant_sha256=_optional_string(payload, "approval_grant_sha256"),
        disposition=EffectDisposition(_string(payload, "disposition")),
        actual_cost_units=_integer(payload, "actual_cost_units"),
        receipt_sha256=_string(payload, "receipt_sha256"),
    )


@dataclass(frozen=True, slots=True)
class JsonSessionStore:
    """Atomic local single-writer store for replayable runtime sessions.

    The lock and atomic replacement make one host restart-safe.  This is deliberately not a
    distributed coordination primitive; a hosted control plane must provide that boundary later.
    """

    root: Path

    def load(self, session_id: str) -> AgentSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        data = _as_object(_JSON_OBJECT.validate_json(path.read_bytes()))
        expected_digest = _string(data, "session_sha256")
        serialized = {key: value for key, value in data.items() if key != "session_sha256"}
        if _json_digest(serialized) != expected_digest:
            raise MarketingRuntimeError("session_json_digest_mismatch")
        budget = _object(data, "budget")
        event_items = _array(data, "events")
        pending_value = data.get("pending_call")
        pending_call = (
            None if pending_value is None else _tool_call_from_json(_as_object(pending_value))
        )
        return AgentSession(
            session_id=_string(data, "session_id"),
            budget=Budget(_integer(budget, "max_tool_calls"), _integer(budget, "max_cost_units")),
            state=RuntimeState(_string(data, "state")),
            spent_cost_units=_integer(data, "spent_cost_units"),
            reserved_cost_units=_integer(data, "reserved_cost_units"),
            tool_calls=_integer(data, "tool_calls"),
            pending_call=pending_call,
            pending_grant_sha256=_optional_string(data, "pending_grant_sha256"),
            execution_started=_boolean(data, "execution_started"),
            dispatched_idempotency_keys=tuple(
                _string_value(item) for item in _array(data, "dispatched_idempotency_keys")
            ),
            consumed_grant_sha256s=tuple(
                _string_value(item) for item in _array(data, "consumed_grant_sha256s")
            ),
            events=tuple(_event_from_json(_as_object(item)) for item in event_items),
        )

    def save(self, session: AgentSession, *, expected_sequence: int) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._lock(session.session_id):
            current = self.load(session.session_id)
            current_sequence = len(current.events) if current else 0
            if current_sequence != expected_sequence:
                raise MarketingRuntimeError("session_compare_and_swap_conflict")
            if len(session.events) < current_sequence:
                raise MarketingRuntimeError("session_event_regression")
            if current is not None and session.events[:current_sequence] != current.events:
                raise MarketingRuntimeError("session_event_history_mismatch")
            self._atomic_write(self._path(session.session_id), _stored_session_json(session))

    def _path(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or "\\" in session_id:
            raise MarketingRuntimeError("invalid_session_id")
        return self.root / f"{session_id}.json"

    def _lock(self, session_id: str) -> _SessionLock:
        return _SessionLock(self.root / f".{session_id}.lock")

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                _ = handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            _ = temporary.replace(path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()


@dataclass(slots=True)
class _SessionLock:
    path: Path
    _handle: TextIO | None = None

    def __enter__(self) -> Self:
        self._handle = self.path.open("a+", encoding="utf-8")
        self.path.chmod(0o600)
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


class MarketingAgentRuntime:
    """Small single-writer harness; model planning stays outside this authority boundary."""

    def _request_tool(
        self,
        session: AgentSession,
        capability: ToolCapability,
        call: ToolCall,
        *,
        now: datetime,
        grant: ApprovalGrant | None = None,
    ) -> AgentSession:
        self._validate_dispatch_state(session)
        self._validate_call(capability, call)
        grant_digest: str | None = None
        if call.effect_class == "external":
            if grant is None:
                return self._append(
                    session,
                    RuntimeState.AWAITING_HUMAN,
                    "tool_approval_required",
                    _reference_payload(call.digest),
                    now,
                )
            if (
                not grant.allows(call, now)
                or grant.remaining_uses != 1
                or grant.digest in session.consumed_grant_sha256s
            ):
                raise MarketingRuntimeError("tool_approval_grant_invalid")
            grant_digest = grant.digest
        if call.idempotency_key in session.dispatched_idempotency_keys:
            raise MarketingRuntimeError("duplicate_tool_idempotency_key")
        if session.tool_calls >= session.budget.max_tool_calls:
            return self._append(
                session,
                RuntimeState.STOPPED,
                "budget_tool_calls_exhausted",
                _reference_payload(call.digest),
                now,
            )
        if (
            session.spent_cost_units
            + session.reserved_cost_units
            + capability.worst_case_cost_units
            > session.budget.max_cost_units
        ):
            return self._append(
                session,
                RuntimeState.STOPPED,
                "budget_cost_exhausted",
                _reference_payload(call.digest),
                now,
            )
        return self._append(
            replace(
                session,
                reserved_cost_units=session.reserved_cost_units + capability.worst_case_cost_units,
                tool_calls=session.tool_calls + 1,
                pending_call=call,
                pending_grant_sha256=grant_digest,
                dispatched_idempotency_keys=(
                    *session.dispatched_idempotency_keys,
                    call.idempotency_key,
                ),
                consumed_grant_sha256s=(
                    *session.consumed_grant_sha256s,
                    *((grant_digest,) if grant_digest is not None else ()),
                ),
            ),
            RuntimeState.EXECUTING,
            "tool_dispatched",
            _tool_call_json(call),
            now,
        )

    def _execute_tool(
        self, session: AgentSession, backend: ToolBackend, *, now: datetime
    ) -> AgentSession:
        if session.state is not RuntimeState.EXECUTING or session.pending_call is None:
            raise MarketingRuntimeError("tool_execution_without_pending_call")
        try:
            receipt = backend.execute(session.pending_call)
        except Exception:  # noqa: BLE001 -- dispatch began; every backend failure is ambiguous.
            return self._reconciliation_required(
                session,
                "tool_backend_exception",
                _reference_payload(session.pending_call.digest),
                now,
            )
        try:
            return self.record_receipt(session, receipt, now=now)
        except MarketingRuntimeError:
            return self._reconciliation_required(
                session,
                "tool_receipt_rejected",
                _reference_payload(receipt.receipt_sha256),
                now,
            )

    def request_persisted_tool(
        self,
        store: SessionStore,
        session: AgentSession,
        admission: ToolAdmission,
        *,
        now: datetime,
    ) -> AgentSession:
        """Commit an admission decision before a future effect executor can see it."""
        self._require_current_session(store, session)
        updated = self._request_tool(
            session,
            admission.capability,
            admission.call,
            now=now,
            grant=admission.grant,
        )
        self._persist_transition(store, session, updated)
        return updated

    def start_persisted_tool_execution(
        self, store: SessionStore, session: AgentSession, *, now: datetime
    ) -> AgentSession:
        """Durably claim the pending call before entering an effect backend."""
        self._require_current_session(store, session)
        if session.state is not RuntimeState.EXECUTING or session.pending_call is None:
            raise MarketingRuntimeError("tool_execution_without_pending_call")
        if session.execution_started:
            raise MarketingRuntimeError("tool_execution_already_started")
        started = self._append(
            replace(session, execution_started=True),
            RuntimeState.EXECUTING,
            "tool_execution_started",
            _reference_payload(session.pending_call.digest),
            now,
        )
        self._persist_transition(store, session, started)
        return started

    def execute_persisted_tool(
        self, store: SessionStore, session: AgentSession, backend: ToolBackend, *, now: datetime
    ) -> AgentSession:
        """Run one durable pending call exactly once or leave it for reconciliation.

        A crash after the execution-start checkpoint never re-enters ``backend.execute`` during
        recovery. It must be resolved through ``reconcile_interrupted_execution``.
        """
        started = self.start_persisted_tool_execution(store, session, now=now)
        result = self._execute_tool(started, backend, now=now)
        try:
            self._persist_transition(store, started, result)
        except MarketingRuntimeError as error:
            raise MarketingRuntimeError(
                "tool_result_persistence_conflict_requires_reconciliation"
            ) from error
        return result

    def reconcile_interrupted_execution(
        self, store: SessionStore, session: AgentSession, *, now: datetime
    ) -> AgentSession:
        """Close a restart-recovered execution claim without retrying its effect."""
        self._require_current_session(store, session)
        if (
            session.state is not RuntimeState.EXECUTING
            or session.pending_call is None
            or not session.execution_started
        ):
            raise MarketingRuntimeError("no_interrupted_tool_execution")
        reconciled = self._reconciliation_required(
            session,
            "tool_execution_interrupted",
            _reference_payload(session.pending_call.digest),
            now,
        )
        self._persist_transition(store, session, reconciled)
        return reconciled

    def append_persisted_event(
        self,
        store: SessionStore,
        session: AgentSession,
        *,
        event_type: str,
        payload: JsonObject,
        now: datetime,
    ) -> AgentSession:
        """Commit a workflow event without granting access to reserved tool event names."""
        if event_type.startswith("tool_"):
            raise MarketingRuntimeError("tool_events_are_runtime_reserved")
        self._require_current_session(store, session)
        updated = self._append(session, session.state, event_type, payload, now)
        self._persist_transition(store, session, updated)
        return updated

    def finalize_persisted_session(
        self,
        store: SessionStore,
        session: AgentSession,
        *,
        state: RuntimeState,
        reason: str,
        now: datetime,
    ) -> AgentSession:
        """Persist an explicit terminal result only after all effects have been resolved."""
        if state not in {RuntimeState.STOPPED, RuntimeState.INCONCLUSIVE, RuntimeState.COMPLETED}:
            raise MarketingRuntimeError("session_final_state_invalid")
        if session.pending_call is not None:
            raise MarketingRuntimeError("session_finalization_has_pending_tool")
        self._require_current_session(store, session)
        updated = self._append(
            session, state, "session_finalized", {"reason": reason, "state": state}, now
        )
        self._persist_transition(store, session, updated)
        return updated

    def record_receipt(
        self, session: AgentSession, receipt: ToolReceipt, *, now: datetime
    ) -> AgentSession:
        if session.state is not RuntimeState.EXECUTING or session.pending_call is None:
            raise MarketingRuntimeError("receipt_without_executing_session")
        if receipt.call_id != session.pending_call.call_id:
            raise MarketingRuntimeError("receipt_call_id_mismatch")
        if receipt.call_sha256 != session.pending_call.digest:
            raise MarketingRuntimeError("receipt_call_digest_mismatch")
        if receipt.approval_grant_sha256 != session.pending_grant_sha256:
            raise MarketingRuntimeError("receipt_approval_grant_mismatch")
        if receipt.actual_cost_units < 0 or receipt.actual_cost_units > session.reserved_cost_units:
            raise MarketingRuntimeError("receipt_cost_outside_reserved_budget")
        state = (
            RuntimeState.AWAITING_RECONCILIATION
            if receipt.disposition is EffectDisposition.UNKNOWN_SIDE_EFFECT
            else RuntimeState.EXECUTING
        )
        return self._append(
            replace(
                session,
                spent_cost_units=session.spent_cost_units + receipt.actual_cost_units,
                reserved_cost_units=0,
                pending_call=None,
                pending_grant_sha256=None,
                execution_started=False,
            ),
            state,
            f"tool_{receipt.disposition}",
            _tool_receipt_json(receipt),
            now,
        )

    @staticmethod
    def _validate_call(capability: ToolCapability, call: ToolCall) -> None:
        if (
            capability.capability_id != call.capability_id
            or capability.descriptor_sha256 != call.descriptor_sha256
            or capability.effect_class != call.effect_class
        ):
            raise MarketingRuntimeError("tool_call_capability_mismatch")

    @staticmethod
    def _validate_dispatch_state(session: AgentSession) -> None:
        if session.state in {
            RuntimeState.STOPPED,
            RuntimeState.INCONCLUSIVE,
            RuntimeState.COMPLETED,
        }:
            raise MarketingRuntimeError("tool_dispatch_after_terminal_state")
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            raise MarketingRuntimeError("tool_dispatch_requires_reconciliation")
        if session.pending_call is not None:
            raise MarketingRuntimeError("tool_dispatch_already_pending")

    def _reconciliation_required(
        self, session: AgentSession, event_type: str, payload: JsonObject, now: datetime
    ) -> AgentSession:
        return self._append(session, RuntimeState.AWAITING_RECONCILIATION, event_type, payload, now)

    @staticmethod
    def _require_current_session(store: SessionStore, session: AgentSession) -> None:
        persisted = store.load(session.session_id)
        if persisted is None and not session.events:
            return
        if persisted != session:
            raise MarketingRuntimeError("session_not_currently_persisted")

    @staticmethod
    def _persist_transition(
        store: SessionStore, previous: AgentSession, updated: AgentSession
    ) -> None:
        store.save(updated, expected_sequence=len(previous.events))

    @staticmethod
    def _append(
        session: AgentSession,
        state: RuntimeState,
        event_type: str,
        payload: JsonObject,
        now: datetime,
    ) -> AgentSession:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise MarketingRuntimeError("event_time_must_be_utc")
        canonical_payload = _JSON_OBJECT.validate_python(payload)
        event = SessionEvent(
            len(session.events) + 1,
            event_type,
            canonical_payload,
            _json_digest(canonical_payload),
            now,
        )
        return replace(session, state=state, events=(*session.events, event))


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _session_json(session: AgentSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "budget": {
            "max_tool_calls": session.budget.max_tool_calls,
            "max_cost_units": session.budget.max_cost_units,
        },
        "state": session.state,
        "spent_cost_units": session.spent_cost_units,
        "reserved_cost_units": session.reserved_cost_units,
        "tool_calls": session.tool_calls,
        "pending_call": (
            None if session.pending_call is None else _tool_call_json(session.pending_call)
        ),
        "pending_grant_sha256": session.pending_grant_sha256,
        "execution_started": session.execution_started,
        "dispatched_idempotency_keys": list(session.dispatched_idempotency_keys),
        "consumed_grant_sha256s": list(session.consumed_grant_sha256s),
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "payload": event.payload,
                "payload_sha256": event.payload_sha256,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in session.events
        ],
    }


def _stored_session_json(session: AgentSession) -> dict[str, object]:
    payload = _session_json(session)
    return {**payload, "session_sha256": _json_digest(payload)}


def _tool_call_json(call: ToolCall) -> JsonObject:
    return _JSON_OBJECT.validate_python(
        {
            "call_id": call.call_id,
            "idempotency_key": call.idempotency_key,
            "capability_id": call.capability_id,
            "descriptor_sha256": call.descriptor_sha256,
            "input_sha256": call.input_sha256,
            "effect_class": call.effect_class,
        }
    )


def _tool_receipt_json(receipt: ToolReceipt) -> JsonObject:
    return _JSON_OBJECT.validate_python(
        {
            "call_id": receipt.call_id,
            "call_sha256": receipt.call_sha256,
            "approval_grant_sha256": receipt.approval_grant_sha256,
            "disposition": receipt.disposition,
            "actual_cost_units": receipt.actual_cost_units,
            "receipt_sha256": receipt.receipt_sha256,
        }
    )


def _reference_payload(reference_sha256: str) -> JsonObject:
    return _JSON_OBJECT.validate_python({"reference_sha256": reference_sha256})


def _tool_call_from_json(value: dict[str, object]) -> ToolCall:
    return ToolCall(
        call_id=_string(value, "call_id"),
        idempotency_key=_string(value, "idempotency_key"),
        capability_id=_string(value, "capability_id"),
        descriptor_sha256=_string(value, "descriptor_sha256"),
        input_sha256=_string(value, "input_sha256"),
        effect_class=_string(value, "effect_class"),
    )


def _event_from_json(value: dict[str, object]) -> SessionEvent:
    payload = _object(value, "payload")
    event = SessionEvent(
        sequence=_integer(value, "sequence"),
        event_type=_string(value, "event_type"),
        payload=_JSON_OBJECT.validate_python(payload),
        payload_sha256=_string(value, "payload_sha256"),
        occurred_at=datetime.fromisoformat(_string(value, "occurred_at")),
    )
    if _json_digest(event.payload) != event.payload_sha256:
        raise MarketingRuntimeError("session_event_payload_digest_mismatch")
    return event


def _json_digest(value: Mapping[str, object]) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _as_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MarketingRuntimeError("session_json_invalid")
    return cast("dict[str, object]", value)


def _object(value: dict[str, object], key: str) -> dict[str, object]:
    try:
        return _as_object(value[key])
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error


def _array(value: dict[str, object], key: str) -> list[object]:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if not isinstance(item, list):
        raise MarketingRuntimeError("session_json_invalid")
    return cast("list[object]", item)


def _string(value: dict[str, object], key: str) -> str:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if not isinstance(item, str):
        raise MarketingRuntimeError("session_json_invalid")
    return item


def _optional_string(value: dict[str, object], key: str) -> str | None:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if item is None:
        return None
    if not isinstance(item, str):
        raise MarketingRuntimeError("session_json_invalid")
    return item


def _string_value(value: object) -> str:
    if not isinstance(value, str):
        raise MarketingRuntimeError("session_json_invalid")
    return value


def _boolean(value: dict[str, object], key: str) -> bool:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if not isinstance(item, bool):
        raise MarketingRuntimeError("session_json_invalid")
    return item


def _integer(value: dict[str, object], key: str) -> int:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if not isinstance(item, int) or isinstance(item, bool):
        raise MarketingRuntimeError("session_json_invalid")
    return item
