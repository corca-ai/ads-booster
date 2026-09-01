"""Provider-neutral, receipt-grounded primitives for the marketing agent runtime.

This module intentionally has no Cloudflare, Appium, Threads, or model-provider import.  It is the
small durable harness boundary that those integrations will later implement as tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path


class RuntimeState(StrEnum):
    CREATED = "created"
    EXECUTING = "executing"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    STOPPED = "stopped"
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


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    call_id: str
    disposition: EffectDisposition
    actual_cost_units: int
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class SessionEvent:
    sequence: int
    event_type: str
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
    events: tuple[SessionEvent, ...] = ()


class ToolBackend(Protocol):
    def execute(self, call: ToolCall) -> ToolReceipt: ...


class SessionStore(Protocol):
    def load(self, session_id: str) -> AgentSession | None: ...

    def save(self, session: AgentSession, *, expected_sequence: int) -> None: ...


class MarketingRuntimeError(ValueError):
    """A deterministic harness admission or receipt error."""


@dataclass(frozen=True, slots=True)
class JsonSessionStore:
    """Serializable local store and CAS boundary for runtime replay tests."""

    root: Path

    def load(self, session_id: str) -> AgentSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        data = cast("dict[str, object]", _JSON_OBJECT.validate_json(path.read_bytes()))
        budget = _object(data, "budget")
        event_items = _array(data, "events")
        return AgentSession(
            session_id=_string(data, "session_id"),
            budget=Budget(_integer(budget, "max_tool_calls"), _integer(budget, "max_cost_units")),
            state=RuntimeState(_string(data, "state")),
            spent_cost_units=_integer(data, "spent_cost_units"),
            reserved_cost_units=_integer(data, "reserved_cost_units"),
            tool_calls=_integer(data, "tool_calls"),
            events=tuple(
                SessionEvent(
                    sequence=_integer(_as_object(item), "sequence"),
                    event_type=_string(_as_object(item), "event_type"),
                    payload_sha256=_string(_as_object(item), "payload_sha256"),
                    occurred_at=datetime.fromisoformat(_string(_as_object(item), "occurred_at")),
                )
                for item in event_items
            ),
        )

    def save(self, session: AgentSession, *, expected_sequence: int) -> None:
        current = self.load(session.session_id)
        current_sequence = len(current.events) if current else 0
        if current_sequence != expected_sequence:
            raise MarketingRuntimeError("session_compare_and_swap_conflict")
        if len(session.events) < current_sequence:
            raise MarketingRuntimeError("session_event_regression")
        _ = self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        serialized = json.dumps(_session_json(session), sort_keys=True)
        _ = self._path(session.session_id).write_text(serialized)

    def _path(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or "\\" in session_id:
            raise MarketingRuntimeError("invalid_session_id")
        return self.root / f"{session_id}.json"


class MarketingAgentRuntime:
    """Small single-writer harness; model planning stays outside this authority boundary."""

    def request_tool(
        self,
        session: AgentSession,
        capability: ToolCapability,
        call: ToolCall,
        *,
        now: datetime,
        grant: ApprovalGrant | None = None,
    ) -> AgentSession:
        self._validate_call(capability, call)
        if call.effect_class == "external" and (grant is None or not grant.allows(call, now)):
            return self._append(
                session, RuntimeState.AWAITING_HUMAN, "tool_approval_required", call.digest, now
            )
        if session.tool_calls >= session.budget.max_tool_calls:
            return self._append(
                session, RuntimeState.STOPPED, "budget_tool_calls_exhausted", call.digest, now
            )
        if (
            session.spent_cost_units
            + session.reserved_cost_units
            + capability.worst_case_cost_units
            > session.budget.max_cost_units
        ):
            return self._append(
                session, RuntimeState.STOPPED, "budget_cost_exhausted", call.digest, now
            )
        return self._append(
            replace(
                session,
                reserved_cost_units=session.reserved_cost_units + capability.worst_case_cost_units,
                tool_calls=session.tool_calls + 1,
            ),
            RuntimeState.EXECUTING,
            "tool_dispatched",
            call.digest,
            now,
        )

    def record_receipt(
        self, session: AgentSession, receipt: ToolReceipt, *, now: datetime
    ) -> AgentSession:
        if session.state is not RuntimeState.EXECUTING:
            raise MarketingRuntimeError("receipt_without_executing_session")
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
            ),
            state,
            f"tool_{receipt.disposition}",
            receipt.receipt_sha256,
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
    def _append(
        session: AgentSession, state: RuntimeState, event_type: str, payload: str, now: datetime
    ) -> AgentSession:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise MarketingRuntimeError("event_time_must_be_utc")
        event = SessionEvent(len(session.events) + 1, event_type, _digest(payload), now)
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
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "payload_sha256": event.payload_sha256,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in session.events
        ],
    }


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


def _integer(value: dict[str, object], key: str) -> int:
    try:
        item = value[key]
    except KeyError as error:
        raise MarketingRuntimeError("session_json_invalid") from error
    if not isinstance(item, int) or isinstance(item, bool):
        raise MarketingRuntimeError("session_json_invalid")
    return item
