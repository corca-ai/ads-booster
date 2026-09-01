"""Provider-neutral, receipt-grounded primitives for the marketing agent runtime.

This module intentionally has no Cloudflare, Appium, Threads, or model-provider import.  It is the
small durable harness boundary that those integrations will later implement as tools.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol


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
    reserved_cost_units: int = 0
    tool_calls: int = 0
    events: tuple[SessionEvent, ...] = ()


class ToolBackend(Protocol):
    def execute(self, call: ToolCall) -> ToolReceipt: ...


class MarketingRuntimeError(ValueError):
    """A deterministic harness admission or receipt error."""


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
            session.reserved_cost_units + capability.worst_case_cost_units
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
            replace(session, reserved_cost_units=receipt.actual_cost_units),
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
