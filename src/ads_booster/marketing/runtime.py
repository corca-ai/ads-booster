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
    from collections.abc import Callable, Mapping
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
_BOUND_TOOL_INVOCATION_SCHEMA_VERSION = "trace.bound-tool-invocation.v1"
_SESSION_SERIALIZATION_VERSION = "trace.marketing-session.v3"
_PREHEADER_SESSION_SERIALIZATION_VERSION = "trace.marketing-session.v2"
_LEGACY_SESSION_SERIALIZATION_VERSION = "trace.marketing-session.v1"
_SESSION_HEADER_SCHEMA_VERSION = "trace.marketing-session-header.v1"
_TERMINAL_STATES = frozenset(
    {RuntimeState.STOPPED, RuntimeState.INCONCLUSIVE, RuntimeState.COMPLETED}
)
_KNOWN_EFFECT_CLASSES = frozenset({"observe", "local_artifact", "control_plane_write", "external"})


@dataclass(frozen=True, slots=True)
class Budget:
    max_tool_calls: int
    max_cost_units: int


@dataclass(frozen=True, slots=True)
class ToolCapability:
    capability_id: str
    descriptor_sha256: str
    request_schema_sha256: str
    effect_class: str
    worst_case_cost_units: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    idempotency_key: str
    capability_id: str
    descriptor_sha256: str
    request_schema_sha256: str
    input_sha256: str
    effect_class: str

    @property
    def digest(self) -> str:
        return _json_digest(
            {
                "schema_version": "trace.tool-call.v1",
                "call_id": self.call_id,
                "idempotency_key": self.idempotency_key,
                "capability_id": self.capability_id,
                "descriptor_sha256": self.descriptor_sha256,
                "request_schema_sha256": self.request_schema_sha256,
                "input_sha256": self.input_sha256,
                "effect_class": self.effect_class,
            }
        )


@dataclass(frozen=True, slots=True)
class BoundToolInvocation:
    """One canonical, non-secret request bound to a descriptor-bound tool call.

    Backends receive this envelope rather than a digest-only call. Connector secrets remain outside
    the request and are resolved by the adapter owner from the capability identity.
    """

    call: ToolCall
    request_json: str
    schema_version: str = _BOUND_TOOL_INVOCATION_SCHEMA_VERSION

    @property
    def request(self) -> JsonObject:
        try:
            return _JSON_OBJECT.validate_json(self.request_json)
        except ValueError as error:
            raise MarketingRuntimeError("tool_invocation_request_invalid") from error

    def validate(self) -> None:
        if self.schema_version != _BOUND_TOOL_INVOCATION_SCHEMA_VERSION:
            raise MarketingRuntimeError("tool_invocation_schema_version_invalid")
        if canonical_json_object(self.request) != self.request_json:
            raise MarketingRuntimeError("tool_invocation_request_not_canonical")
        if self.call.input_sha256 != _invocation_input_sha256(
            self.schema_version,
            self.call.request_schema_sha256,
            self.request,
        ):
            raise MarketingRuntimeError("tool_invocation_input_digest_mismatch")


def bind_tool_invocation(
    capability: ToolCapability,
    *,
    call_id: str,
    idempotency_key: str,
    request: JsonObject,
) -> BoundToolInvocation:
    """Create the only supported call/request binding for a capability handoff."""
    _validate_effect_class(capability.effect_class)
    request_json = canonical_json_object(request)
    invocation = BoundToolInvocation(
        ToolCall(
            call_id=call_id,
            idempotency_key=idempotency_key,
            capability_id=capability.capability_id,
            descriptor_sha256=capability.descriptor_sha256,
            request_schema_sha256=capability.request_schema_sha256,
            input_sha256=_invocation_input_sha256(
                _BOUND_TOOL_INVOCATION_SCHEMA_VERSION,
                capability.request_schema_sha256,
                _JSON_OBJECT.validate_json(request_json),
            ),
            effect_class=capability.effect_class,
        ),
        request_json,
    )
    invocation.validate()
    return invocation


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
    invocation: BoundToolInvocation
    grant: ApprovalGrant | None = None

    @property
    def call(self) -> ToolCall:
        return self.invocation.call


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
    pending_invocation: BoundToolInvocation | None = None
    pending_grant_sha256: str | None = None
    execution_started: bool = False
    dispatched_idempotency_keys: tuple[str, ...] = ()
    consumed_grant_sha256s: tuple[str, ...] = ()
    events: tuple[SessionEvent, ...] = ()
    serialization_version: str = _SESSION_SERIALIZATION_VERSION


@dataclass(frozen=True, slots=True)
class _DerivedRuntimeCheckpoint:
    """Runtime-owned state reconstructed from append-only runtime events only."""

    session_id: str
    budget: Budget
    state: RuntimeState
    spent_cost_units: int
    reserved_cost_units: int
    tool_calls: int
    pending_invocation: BoundToolInvocation | None
    pending_grant_sha256: str | None
    execution_started: bool
    dispatched_idempotency_keys: tuple[str, ...]
    consumed_grant_sha256s: tuple[str, ...]

    @property
    def pending_call(self) -> ToolCall | None:
        return None if self.pending_invocation is None else self.pending_invocation.call


class ToolBackend(Protocol):
    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt: ...


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


def session_trace_sha256(session: AgentSession) -> str:
    """Return stable provenance for the observable, append-only event trace."""
    return _json_digest(
        {
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload_sha256": event.payload_sha256,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                for event in session.events
            ]
        }
    )


def replay_session(events: tuple[SessionEvent, ...]) -> AgentSession:
    """Reconstruct the authoritative runtime checkpoint from a v3 append-only event trace.

    Callers that only have an exported trace must use this reducer rather than trusting a separately
    supplied session checkpoint.  It deliberately returns no pending invocation after a terminal
    trace unless the ledger itself proves that state.
    """
    derived = _replay_runtime_events(events)
    return AgentSession(
        session_id=derived.session_id,
        budget=derived.budget,
        state=derived.state,
        spent_cost_units=derived.spent_cost_units,
        reserved_cost_units=derived.reserved_cost_units,
        tool_calls=derived.tool_calls,
        pending_call=derived.pending_call,
        pending_invocation=derived.pending_invocation,
        pending_grant_sha256=derived.pending_grant_sha256,
        execution_started=derived.execution_started,
        dispatched_idempotency_keys=derived.dispatched_idempotency_keys,
        consumed_grant_sha256s=derived.consumed_grant_sha256s,
        events=events,
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
        version_value = data.get("serialization_version")
        serialization_version = (
            _LEGACY_SESSION_SERIALIZATION_VERSION
            if version_value is None
            else _string(data, "serialization_version")
        )
        session_digest = (
            _legacy_json_digest(serialized)
            if serialization_version == _LEGACY_SESSION_SERIALIZATION_VERSION
            else _json_digest(serialized)
        )
        if serialization_version not in {
            _LEGACY_SESSION_SERIALIZATION_VERSION,
            _PREHEADER_SESSION_SERIALIZATION_VERSION,
            _SESSION_SERIALIZATION_VERSION,
        }:
            raise MarketingRuntimeError("session_serialization_version_invalid")
        if session_digest != expected_digest:
            raise MarketingRuntimeError("session_json_digest_mismatch")
        budget = _object(data, "budget")
        event_items = _array(data, "events")
        pending_value = data.get("pending_call")
        if pending_value is not None and serialization_version != _SESSION_SERIALIZATION_VERSION:
            raise MarketingRuntimeError("legacy_pending_session_unverifiable")
        pending_call = (
            None if pending_value is None else _tool_call_from_json(_as_object(pending_value))
        )
        pending_invocation_value = data.get("pending_invocation")
        pending_invocation = (
            None
            if pending_invocation_value is None
            else _tool_invocation_from_json(_as_object(pending_invocation_value))
        )
        session = AgentSession(
            session_id=_string(data, "session_id"),
            budget=Budget(_integer(budget, "max_tool_calls"), _integer(budget, "max_cost_units")),
            state=RuntimeState(_string(data, "state")),
            spent_cost_units=_integer(data, "spent_cost_units"),
            reserved_cost_units=_integer(data, "reserved_cost_units"),
            tool_calls=_integer(data, "tool_calls"),
            pending_call=pending_call,
            pending_invocation=pending_invocation,
            pending_grant_sha256=_optional_string(data, "pending_grant_sha256"),
            execution_started=_boolean(data, "execution_started"),
            dispatched_idempotency_keys=tuple(
                _string_value(item) for item in _array(data, "dispatched_idempotency_keys")
            ),
            consumed_grant_sha256s=tuple(
                _string_value(item) for item in _array(data, "consumed_grant_sha256s")
            ),
            events=tuple(
                _event_from_json(
                    _as_object(item),
                    legacy_digest=serialization_version == _LEGACY_SESSION_SERIALIZATION_VERSION,
                )
                for item in event_items
            ),
            serialization_version=serialization_version,
        )
        if serialization_version != _SESSION_SERIALIZATION_VERSION:
            if session.state not in _TERMINAL_STATES:
                raise MarketingRuntimeError("legacy_nonterminal_session_unverifiable")
            return session
        _validate_loaded_session_checkpoint(session)
        return session

    def save(self, session: AgentSession, *, expected_sequence: int) -> None:
        if session.serialization_version != _SESSION_SERIALIZATION_VERSION:
            raise MarketingRuntimeError("legacy_session_read_only")
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
            _validate_loaded_session_checkpoint(session)
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
        invocation: BoundToolInvocation,
        *,
        now: datetime,
        grant: ApprovalGrant | None = None,
    ) -> AgentSession:
        call = invocation.call
        self._validate_dispatch_state(session)
        self._validate_invocation(capability, invocation)
        grant_digest: str | None = None
        if _effect_requires_approval(call.effect_class):
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
                pending_invocation=invocation,
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
            _tool_dispatch_json(
                invocation,
                grant_digest,
                capability.worst_case_cost_units,
            ),
            now,
        )

    def _execute_tool(
        self, session: AgentSession, backend: ToolBackend, *, now: datetime
    ) -> AgentSession:
        if session.state is not RuntimeState.EXECUTING or session.pending_call is None:
            raise MarketingRuntimeError("tool_execution_without_pending_call")
        if session.pending_invocation is None:
            return self._reconciliation_required(
                session,
                "tool_invocation_missing",
                _reference_payload(session.pending_call.digest),
                now,
            )
        try:
            self._validate_pending_invocation(session)
        except MarketingRuntimeError:
            return self._reconciliation_required(
                session,
                "tool_invocation_rejected",
                _reference_payload(session.pending_call.digest),
                now,
            )
        try:
            receipt = backend.execute(session.pending_invocation)
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
            admission.invocation,
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
        self._validate_pending_invocation(session)
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

    def resolve_persisted_reconciliation(
        self,
        store: SessionStore,
        session: AgentSession,
        receipt: ToolReceipt,
        *,
        now: datetime,
    ) -> AgentSession:
        """Persist a terminal GET-derived receipt for one ambiguous, never-retried effect."""
        self._require_current_session(store, session)
        if (
            session.state is not RuntimeState.AWAITING_RECONCILIATION
            or session.pending_call is None
            or not session.execution_started
            or receipt.disposition is EffectDisposition.UNKNOWN_SIDE_EFFECT
        ):
            raise MarketingRuntimeError("reconciliation_receipt_invalid")
        resolved = self._record_receipt(session, receipt, now=now)
        self._persist_transition(store, session, resolved)
        return resolved

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
        if _is_runtime_reserved_event(event_type):
            raise MarketingRuntimeError("tool_events_are_runtime_reserved")
        if session.state in _TERMINAL_STATES:
            raise MarketingRuntimeError("session_event_after_terminal_state")
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
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            raise MarketingRuntimeError("session_finalization_requires_reconciliation")
        if any(event.event_type == "session_finalized" for event in session.events):
            raise MarketingRuntimeError("session_already_finalized")
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
        return self._record_receipt(session, receipt, now=now)

    def _record_receipt(
        self, session: AgentSession, receipt: ToolReceipt, *, now: datetime
    ) -> AgentSession:
        if session.pending_call is None:
            raise MarketingRuntimeError("receipt_without_pending_call")
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
                pending_invocation=None,
                pending_grant_sha256=None,
                execution_started=False,
            ),
            state,
            f"tool_{receipt.disposition}",
            _tool_receipt_json(receipt),
            now,
        )

    @staticmethod
    def _validate_invocation(capability: ToolCapability, invocation: BoundToolInvocation) -> None:
        invocation.validate()
        call = invocation.call
        _validate_effect_class(capability.effect_class)
        _validate_effect_class(call.effect_class)
        if (
            capability.capability_id != call.capability_id
            or capability.descriptor_sha256 != call.descriptor_sha256
            or capability.request_schema_sha256 != call.request_schema_sha256
            or capability.effect_class != call.effect_class
        ):
            raise MarketingRuntimeError("tool_call_capability_mismatch")

    @staticmethod
    def _validate_pending_invocation(session: AgentSession) -> None:
        if session.pending_call is None or session.pending_invocation is None:
            raise MarketingRuntimeError("tool_invocation_missing")
        if session.pending_invocation.call != session.pending_call:
            raise MarketingRuntimeError("tool_invocation_call_mismatch")
        session.pending_invocation.validate()

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
        if event_type != "session_started" and not session.events:
            session = MarketingAgentRuntime._append(
                session,
                RuntimeState.CREATED,
                "session_started",
                _session_header_payload(session),
                now,
            )
        if event_type == "session_started" and session.events:
            raise MarketingRuntimeError("session_header_already_started")
        if event_type != "session_finalized" and any(
            item.event_type == "session_finalized" for item in session.events
        ):
            raise MarketingRuntimeError("session_event_after_finalization")
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
        "serialization_version": session.serialization_version,
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
        "pending_invocation": (
            None
            if session.pending_invocation is None
            else _tool_invocation_json(session.pending_invocation)
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
            "request_schema_sha256": call.request_schema_sha256,
            "input_sha256": call.input_sha256,
            "effect_class": call.effect_class,
        }
    )


def _tool_invocation_json(invocation: BoundToolInvocation) -> JsonObject:
    invocation.validate()
    return _JSON_OBJECT.validate_python(
        {
            "schema_version": invocation.schema_version,
            "call": _tool_call_json(invocation.call),
            "request": invocation.request,
        }
    )


def _tool_dispatch_json(
    invocation: BoundToolInvocation,
    approval_grant_sha256: str | None,
    reserved_cost_units: int,
) -> JsonObject:
    return _JSON_OBJECT.validate_python(
        {
            "invocation": _tool_invocation_json(invocation),
            "approval_grant_sha256": approval_grant_sha256,
            "reserved_cost_units": reserved_cost_units,
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
        request_schema_sha256=_string(value, "request_schema_sha256"),
        input_sha256=_string(value, "input_sha256"),
        effect_class=_string(value, "effect_class"),
    )


def _tool_invocation_from_json(value: dict[str, object]) -> BoundToolInvocation:
    invocation = BoundToolInvocation(
        call=_tool_call_from_json(_object(value, "call")),
        request_json=canonical_json_object(_JSON_OBJECT.validate_python(_object(value, "request"))),
        schema_version=_string(value, "schema_version"),
    )
    invocation.validate()
    return invocation


def _event_from_json(value: dict[str, object], *, legacy_digest: bool = False) -> SessionEvent:
    payload = _object(value, "payload")
    event = SessionEvent(
        sequence=_integer(value, "sequence"),
        event_type=_string(value, "event_type"),
        payload=_JSON_OBJECT.validate_python(payload),
        payload_sha256=_string(value, "payload_sha256"),
        occurred_at=datetime.fromisoformat(_string(value, "occurred_at")),
    )
    payload_digest = (
        _legacy_json_digest(event.payload) if legacy_digest else _json_digest(event.payload)
    )
    if payload_digest != event.payload_sha256:
        raise MarketingRuntimeError("session_event_payload_digest_mismatch")
    return event


def canonical_json_object(value: JsonObject) -> str:
    """Encode one non-secret object with the canonical UTF-8 JSON policy used for bindings."""
    canonical = _JSON_OBJECT.validate_python(value)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_sha256(value: JsonObject) -> str:
    """Digest a canonical object with the same policy used by bound tool requests."""
    return _digest(canonical_json_object(value))


def _invocation_input_sha256(
    schema_version: str, request_schema_sha256: str, request: JsonObject
) -> str:
    """Bind a canonical request to the concrete request-schema version that owns it."""
    return canonical_json_sha256(
        _JSON_OBJECT.validate_python(
            {
                "schema_version": schema_version,
                "request_schema_sha256": request_schema_sha256,
                "request": request,
            }
        )
    )


def _json_digest(value: Mapping[str, object]) -> str:
    return canonical_json_sha256(_JSON_OBJECT.validate_python(value))


def _legacy_json_digest(value: Mapping[str, object]) -> str:
    """Verify versionless v1 storage without upgrading its digest or execution authority."""
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _validate_loaded_session_checkpoint(session: AgentSession) -> None:
    """Reject a checkpoint that could redeliver a call after its persisted start marker."""
    if session.pending_call is None:
        if session.pending_invocation is not None or session.execution_started:
            raise MarketingRuntimeError("session_pending_invocation_checkpoint_mismatch")
    elif (
        session.pending_invocation is None
        or session.pending_invocation.call != session.pending_call
    ):
        raise MarketingRuntimeError("session_pending_invocation_checkpoint_mismatch")
    _validate_event_ledger_checkpoint(session)


def _validate_event_ledger_checkpoint(session: AgentSession) -> None:
    """Treat the serialized checkpoint as a cache, never as independent tool authority."""
    derived = _replay_runtime_events(session.events)
    _validate_checkpoint_matches(
        (
            (derived.session_id, session.session_id, "session_header_checkpoint_mismatch"),
            (derived.budget, session.budget, "session_budget_checkpoint_mismatch"),
            (derived.state, session.state, "session_state_checkpoint_mismatch"),
            (
                derived.spent_cost_units,
                session.spent_cost_units,
                "session_spent_budget_checkpoint_mismatch",
            ),
            (
                derived.reserved_cost_units,
                session.reserved_cost_units,
                "session_pending_budget_checkpoint_mismatch",
            ),
            (derived.tool_calls, session.tool_calls, "session_tool_calls_checkpoint_mismatch"),
            (
                (derived.pending_call, derived.pending_invocation),
                (session.pending_call, session.pending_invocation),
                "session_pending_dispatch_checkpoint_mismatch",
            ),
            (
                derived.pending_grant_sha256,
                session.pending_grant_sha256,
                "session_pending_grant_checkpoint_mismatch",
            ),
            (
                derived.execution_started,
                session.execution_started,
                "session_execution_checkpoint_mismatch",
            ),
            (
                derived.dispatched_idempotency_keys,
                session.dispatched_idempotency_keys,
                "session_idempotency_checkpoint_mismatch",
            ),
            (
                derived.consumed_grant_sha256s,
                session.consumed_grant_sha256s,
                "session_grant_consumption_checkpoint_mismatch",
            ),
        )
    )


def _validate_checkpoint_matches(
    checks: tuple[tuple[object, object, str], ...],
) -> None:
    for derived, checkpoint, error in checks:
        if derived != checkpoint:
            raise MarketingRuntimeError(error)


def _replay_runtime_events(events: tuple[SessionEvent, ...]) -> _DerivedRuntimeCheckpoint:
    """Pure, fail-closed reducer for every runtime-controlled checkpoint field."""
    replay = _RuntimeReplay(
        session_id=None,
        budget=None,
        state=RuntimeState.CREATED,
        spent_cost_units=0,
        reserved_cost_units=0,
        tool_calls=0,
        pending_invocation=None,
        pending_grant_sha256=None,
        execution_started=False,
        dispatched_idempotency_keys=[],
        consumed_grant_sha256s=[],
        finalized=False,
        last_occurred_at=None,
    )
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise MarketingRuntimeError("session_event_sequence_mismatch")
        _validate_runtime_event_integrity(replay, event)
        _apply_runtime_event(replay, event)
    return replay.checkpoint()


@dataclass(slots=True)
class _RuntimeReplay:
    session_id: str | None
    budget: Budget | None
    state: RuntimeState
    spent_cost_units: int
    reserved_cost_units: int
    tool_calls: int
    pending_invocation: BoundToolInvocation | None
    pending_grant_sha256: str | None
    execution_started: bool
    dispatched_idempotency_keys: list[str]
    consumed_grant_sha256s: list[str]
    finalized: bool
    last_occurred_at: datetime | None

    def checkpoint(self) -> _DerivedRuntimeCheckpoint:
        if self.session_id is None or self.budget is None:
            raise MarketingRuntimeError("session_event_header_missing")
        return _DerivedRuntimeCheckpoint(
            session_id=self.session_id,
            budget=self.budget,
            state=self.state,
            spent_cost_units=self.spent_cost_units,
            reserved_cost_units=self.reserved_cost_units,
            tool_calls=self.tool_calls,
            pending_invocation=self.pending_invocation,
            pending_grant_sha256=self.pending_grant_sha256,
            execution_started=self.execution_started,
            dispatched_idempotency_keys=tuple(self.dispatched_idempotency_keys),
            consumed_grant_sha256s=tuple(self.consumed_grant_sha256s),
        )


def _validate_runtime_event_integrity(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if event.payload_sha256 != _json_digest(event.payload):
        raise MarketingRuntimeError("session_event_payload_digest_mismatch")
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() != UTC.utcoffset(
        event.occurred_at
    ):
        raise MarketingRuntimeError("session_event_time_must_be_utc")
    if replay.last_occurred_at is not None and event.occurred_at < replay.last_occurred_at:
        raise MarketingRuntimeError("session_event_time_non_monotonic")
    replay.last_occurred_at = event.occurred_at


def _apply_runtime_event(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if event.event_type == "session_started":
        _apply_session_started(replay, event)
        return
    if replay.budget is None:
        raise MarketingRuntimeError("session_event_header_missing")
    if replay.finalized:
        raise MarketingRuntimeError("session_event_after_finalization")
    handler = _runtime_event_handler(event.event_type)
    if handler is not None:
        handler(replay, event)
    elif _is_runtime_reserved_event(event.event_type):
        raise MarketingRuntimeError("session_event_transition_invalid")


def _apply_session_started(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if event.sequence != 1 or replay.session_id is not None:
        raise MarketingRuntimeError("session_event_transition_invalid")
    session_id, budget, header_sha256 = _session_header_from_event(event)
    if header_sha256 != _session_header_sha256(session_id, budget):
        raise MarketingRuntimeError("session_event_header_invalid")
    replay.session_id = session_id
    replay.budget = budget


def _runtime_event_handler(
    event_type: str,
) -> Callable[[_RuntimeReplay, SessionEvent], None] | None:
    return {
        "tool_approval_required": _apply_approval_required,
        "tool_dispatched": _apply_dispatch,
        "tool_execution_started": _apply_execution_started,
        "budget_tool_calls_exhausted": _apply_budget_stop,
        "budget_cost_exhausted": _apply_budget_stop,
        "session_finalized": _apply_finalization,
        **{f"tool_{item}": _apply_receipt for item in EffectDisposition},
        **dict.fromkeys(_RECONCILIATION_EVENTS, _apply_reconciliation_required),
    }.get(event_type)


_RECONCILIATION_EVENTS = frozenset(
    {
        "tool_backend_exception",
        "tool_execution_interrupted",
        "tool_invocation_missing",
        "tool_invocation_rejected",
        "tool_receipt_rejected",
    }
)


def _apply_approval_required(replay: _RuntimeReplay, event: SessionEvent) -> None:
    _require_dispatchable(replay)
    _ = _reference_from_event(event)
    replay.state = RuntimeState.AWAITING_HUMAN


def _apply_dispatch(replay: _RuntimeReplay, event: SessionEvent) -> None:
    _require_dispatchable(replay)
    invocation, grant_sha256, reserved_cost = _tool_dispatch_from_event(event)
    if replay.budget is None:
        raise MarketingRuntimeError("session_event_header_missing")
    if reserved_cost < 0 or replay.spent_cost_units + reserved_cost > replay.budget.max_cost_units:
        raise MarketingRuntimeError("session_event_budget_invalid")
    if invocation.call.idempotency_key in replay.dispatched_idempotency_keys:
        raise MarketingRuntimeError("session_event_idempotency_duplicate")
    _validate_replayed_grant(replay, invocation, grant_sha256)
    replay.pending_invocation = invocation
    replay.pending_grant_sha256 = grant_sha256
    replay.reserved_cost_units = reserved_cost
    replay.tool_calls += 1
    replay.dispatched_idempotency_keys.append(invocation.call.idempotency_key)
    if replay.tool_calls > replay.budget.max_tool_calls:
        raise MarketingRuntimeError("session_event_budget_invalid")
    replay.state = RuntimeState.EXECUTING


def _apply_execution_started(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if (
        replay.pending_invocation is None
        or replay.execution_started
        or _reference_from_event(event) != replay.pending_invocation.call.digest
    ):
        raise MarketingRuntimeError("session_event_transition_invalid")
    replay.execution_started = True
    replay.state = RuntimeState.EXECUTING


def _apply_receipt(replay: _RuntimeReplay, event: SessionEvent) -> None:
    receipt = tool_receipt_from_event(event)
    if (
        replay.pending_invocation is None
        or not replay.execution_started
        or receipt.call_id != replay.pending_invocation.call.call_id
        or receipt.call_sha256 != replay.pending_invocation.call.digest
        or receipt.approval_grant_sha256 != replay.pending_grant_sha256
        or receipt.actual_cost_units < 0
        or receipt.actual_cost_units > replay.reserved_cost_units
        or event.event_type != f"tool_{receipt.disposition}"
    ):
        raise MarketingRuntimeError("session_event_receipt_invalid")
    replay.spent_cost_units += receipt.actual_cost_units
    replay.reserved_cost_units = 0
    replay.pending_invocation = None
    replay.pending_grant_sha256 = None
    replay.execution_started = False
    replay.state = (
        RuntimeState.AWAITING_RECONCILIATION
        if receipt.disposition is EffectDisposition.UNKNOWN_SIDE_EFFECT
        else RuntimeState.EXECUTING
    )


def _apply_reconciliation_required(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if replay.pending_invocation is None or not replay.execution_started:
        raise MarketingRuntimeError("session_event_transition_invalid")
    _ = _reference_from_event(event)
    replay.state = RuntimeState.AWAITING_RECONCILIATION


def _apply_budget_stop(replay: _RuntimeReplay, event: SessionEvent) -> None:
    _require_dispatchable(replay)
    _ = _reference_from_event(event)
    replay.state = RuntimeState.STOPPED


def _apply_finalization(replay: _RuntimeReplay, event: SessionEvent) -> None:
    if (
        replay.pending_invocation is not None
        or replay.state is RuntimeState.AWAITING_RECONCILIATION
    ):
        raise MarketingRuntimeError("session_event_transition_invalid")
    final_state = RuntimeState(_string(_as_object(event.payload), "state"))
    if final_state not in _TERMINAL_STATES:
        raise MarketingRuntimeError("session_event_transition_invalid")
    if replay.state in _TERMINAL_STATES and final_state is not replay.state:
        raise MarketingRuntimeError("session_event_transition_invalid")
    replay.state = final_state
    replay.finalized = True


def _require_dispatchable(replay: _RuntimeReplay) -> None:
    if replay.pending_invocation is not None or replay.state in {
        RuntimeState.AWAITING_RECONCILIATION,
        RuntimeState.STOPPED,
        RuntimeState.INCONCLUSIVE,
        RuntimeState.COMPLETED,
    }:
        raise MarketingRuntimeError("session_event_transition_invalid")


def _validate_replayed_grant(
    replay: _RuntimeReplay, invocation: BoundToolInvocation, grant_sha256: str | None
) -> None:
    requires_approval = _effect_requires_approval(invocation.call.effect_class)
    if requires_approval and grant_sha256 is None:
        raise MarketingRuntimeError("session_event_approval_missing")
    if not requires_approval and grant_sha256 is not None:
        raise MarketingRuntimeError("session_event_approval_unexpected")
    if grant_sha256 is not None:
        if grant_sha256 in replay.consumed_grant_sha256s:
            raise MarketingRuntimeError("session_event_grant_duplicate")
        replay.consumed_grant_sha256s.append(grant_sha256)


def _validate_effect_class(effect_class: str) -> None:
    if effect_class not in _KNOWN_EFFECT_CLASSES:
        raise MarketingRuntimeError("tool_effect_class_invalid")


def _effect_requires_approval(effect_class: str) -> bool:
    _validate_effect_class(effect_class)
    return effect_class == "external"


def _is_runtime_reserved_event(event_type: str) -> bool:
    return event_type.startswith(("tool_", "budget_")) or event_type in {
        "session_started",
        "session_finalized",
    }


def _session_header_payload(session: AgentSession) -> JsonObject:
    budget = session.budget
    return _JSON_OBJECT.validate_python(
        {
            "schema_version": _SESSION_HEADER_SCHEMA_VERSION,
            "session_id": session.session_id,
            "budget": {
                "max_tool_calls": budget.max_tool_calls,
                "max_cost_units": budget.max_cost_units,
            },
            "header_sha256": _session_header_sha256(session.session_id, budget),
        }
    )


def _session_header_from_event(event: SessionEvent) -> tuple[str, Budget, str]:
    payload = _as_object(event.payload)
    if _string(payload, "schema_version") != _SESSION_HEADER_SCHEMA_VERSION:
        raise MarketingRuntimeError("session_event_header_invalid")
    budget = _object(payload, "budget")
    return (
        _string(payload, "session_id"),
        Budget(
            _integer(budget, "max_tool_calls"),
            _integer(budget, "max_cost_units"),
        ),
        _string(payload, "header_sha256"),
    )


def _session_header_sha256(session_id: str, budget: Budget) -> str:
    return _json_digest(
        {
            "schema_version": _SESSION_HEADER_SCHEMA_VERSION,
            "session_id": session_id,
            "budget": {
                "max_tool_calls": budget.max_tool_calls,
                "max_cost_units": budget.max_cost_units,
            },
        }
    )


def _reference_from_event(event: SessionEvent) -> str:
    return _string(_as_object(event.payload), "reference_sha256")


def _tool_dispatch_from_event(event: SessionEvent) -> tuple[BoundToolInvocation, str | None, int]:
    try:
        payload = _as_object(event.payload)
        return (
            _tool_invocation_from_json(_object(payload, "invocation")),
            _optional_string(payload, "approval_grant_sha256"),
            _integer(payload, "reserved_cost_units"),
        )
    except MarketingRuntimeError as error:
        raise MarketingRuntimeError("session_tool_dispatch_invalid") from error


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
