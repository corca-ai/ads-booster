"""Portable domain records owned by the on-premises Marketing Agent Service."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.contracts.tool_capability import ToolDescriptor  # noqa: TC001
from ads_booster.transport.json_types import JsonObject  # noqa: TC001

BoundedId = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]


class AgentRunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class AgentStepKind(StrEnum):
    OBSERVE = "observe"
    PLAN = "plan"
    APPROVE = "approve"
    EXECUTE = "execute"
    VERIFY = "verify"
    EVALUATE = "evaluate"
    REPLAN = "replan"


class AgentRecordKind(StrEnum):
    CAPABILITY_SNAPSHOT = "capability_snapshot"
    INTENT = "intent"
    INVOCATION = "invocation"
    APPROVAL = "approval"
    RECEIPT = "receipt"
    OUTCOME = "outcome"
    LEARNING = "learning"
    EVIDENCE = "evidence"
    REASONING = "reasoning"


class AgentGoal(ContractModel):
    objective: Annotated[str, Field(min_length=1, max_length=20_000)]
    success_criteria: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    context: JsonObject = Field(default_factory=dict)


class AgentBudget(ContractModel):
    max_tool_calls: Annotated[int, Field(ge=0, le=10_000)]
    max_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]


class AgentRun(ContractModel):
    schema_version: Literal["trace.agent-run.v1"]
    run_id: BoundedId
    tenant_id: BoundedId
    goal: AgentGoal
    budget: AgentBudget
    state: AgentRunState = AgentRunState.CREATED
    revision: Annotated[int, Field(ge=1)] = 1
    head_step_sha256: Sha256Digest | None = None
    blocked_reason: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_utc_times(self) -> Self:
        _require_utc(self.created_at)
        _require_utc(self.updated_at)
        if self.updated_at < self.created_at:
            message = "run updated time precedes creation"
            raise ValueError(message)
        return self


class AgentStep(ContractModel):
    schema_version: Literal["trace.agent-step.v1"]
    step_id: BoundedId
    run_id: BoundedId
    sequence: Annotated[int, Field(ge=1)]
    kind: AgentStepKind
    state: Literal["proposed", "awaiting_approval", "executing", "completed", "failed"]
    input_sha256: Sha256Digest
    output_sha256: Sha256Digest | None = None
    parent_step_sha256: Sha256Digest | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def require_completed_output(self) -> Self:
        _require_utc(self.occurred_at)
        if (self.state == "completed") != (self.output_sha256 is not None):
            message = "completed steps require output and incomplete steps cannot claim it"
            raise ValueError(message)
        return self


class AgentRunEvent(ContractModel):
    """Authoritative append-only event from which the run projection is rebuilt."""

    schema_version: Literal["trace.agent-run-event.v1"]
    run_id: BoundedId
    sequence: Annotated[int, Field(ge=1)]
    event_type: Literal["run_created", "step_appended"]
    previous_event_sha256: Sha256Digest | None = None
    run: AgentRun
    step_sha256: Sha256Digest | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def require_event_lineage(self) -> Self:
        _require_utc(self.occurred_at)
        if self.run.run_id != self.run_id or self.run.revision != self.sequence:
            message = "agent run event does not match its run projection"
            raise ValueError(message)
        is_created = self.event_type == "run_created"
        if is_created != (self.sequence == 1):
            message = "only the first run event may create the run"
            raise ValueError(message)
        if is_created != (self.previous_event_sha256 is None):
            message = "run event parent binding is inconsistent"
            raise ValueError(message)
        if is_created == (self.step_sha256 is not None):
            message = "step events require exactly one step digest"
            raise ValueError(message)
        if self.step_sha256 is not None and self.run.head_step_sha256 != self.step_sha256:
            message = "run event head does not match its step"
            raise ValueError(message)
        return self


class AgentRecord(ContractModel):
    """Append-only storage envelope for one typed portable contract."""

    schema_version: Literal["trace.agent-record.v1"]
    record_id: BoundedId
    run_id: BoundedId
    kind: AgentRecordKind
    payload_schema_version: Annotated[str, Field(min_length=1, max_length=160)]
    payload: JsonObject
    payload_sha256: Sha256Digest
    occurred_at: datetime

    @model_validator(mode="after")
    def require_payload_binding(self) -> Self:
        _require_utc(self.occurred_at)
        if self.payload.get("schema_version") != self.payload_schema_version:
            message = "agent record schema version does not match its payload"
            raise ValueError(message)
        if contract_sha256(self.payload) != self.payload_sha256:
            message = "agent record payload digest mismatch"
            raise ValueError(message)
        return self


class AgentIntent(ContractModel):
    schema_version: Literal["trace.agent-intent.v1"]
    intent_id: BoundedId
    run_id: BoundedId
    step_id: BoundedId
    action: Annotated[str, Field(min_length=1, max_length=160)]
    capability_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    evidence_sha256s: Annotated[tuple[Sha256Digest, ...], Field(max_length=128)] = ()
    expected_outcome: Annotated[str, Field(min_length=1, max_length=2000)]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=4000)]


class CapabilitySnapshot(ContractModel):
    schema_version: Literal["trace.capability-snapshot.v1"]
    snapshot_id: BoundedId
    run_id: BoundedId
    descriptors: Annotated[tuple[ToolDescriptor, ...], Field(max_length=256)]
    created_at: datetime

    @model_validator(mode="after")
    def require_unique_ready_descriptors(self) -> Self:
        _require_utc(self.created_at)
        keys = tuple((item.capability_id, item.version) for item in self.descriptors)
        if len(keys) != len(set(keys)):
            message = "capability snapshot descriptors must be unique"
            raise ValueError(message)
        if any(not item.enabled or not item.readiness.ready for item in self.descriptors):
            message = "capability snapshots contain only selectable descriptors"
            raise ValueError(message)
        return self

    @property
    def digest(self) -> str:
        return contract_sha256(self)


class ToolInvocation(ContractModel):
    schema_version: Literal["trace.tool-invocation.v1"]
    invocation_id: BoundedId
    run_id: BoundedId
    step_id: BoundedId
    intent_sha256: Sha256Digest
    capability_snapshot_sha256: Sha256Digest
    descriptor_sha256: Sha256Digest
    idempotency_key: Annotated[str, Field(min_length=1, max_length=512)]
    input: JsonObject
    input_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_input_binding(self) -> Self:
        if contract_sha256(self.input) != self.input_sha256:
            message = "tool invocation input digest mismatch"
            raise ValueError(message)
        return self


class ToolApproval(ContractModel):
    schema_version: Literal["trace.tool-approval.v1"]
    approval_id: BoundedId
    invocation_sha256: Sha256Digest
    approver_id: BoundedId
    decision: Literal["granted", "rejected", "revoked"]
    expires_at: datetime | None = None
    decided_at: datetime

    @model_validator(mode="after")
    def require_approval_times(self) -> Self:
        _require_utc(self.decided_at)
        if self.expires_at is not None:
            _require_utc(self.expires_at)
        if self.decision == "granted" and self.expires_at is None:
            message = "granted approvals require expiry"
            raise ValueError(message)
        return self


class ToolReceiptRecord(ContractModel):
    schema_version: Literal["trace.tool-receipt.v1"]
    receipt_id: BoundedId
    invocation_sha256: Sha256Digest
    approval_sha256: Sha256Digest | None = None
    disposition: Literal["no_effect", "succeeded", "failed", "unknown_side_effect"]
    actual_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]
    output_schema_sha256: Sha256Digest
    output_sha256: Sha256Digest
    executor_id: BoundedId
    reconciliation_sha256: Sha256Digest | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def require_receipt_time(self) -> Self:
        _require_utc(self.occurred_at)
        return self


class AgentOutcome(ContractModel):
    schema_version: Literal["trace.agent-outcome.v1"]
    outcome_id: BoundedId
    run_id: BoundedId
    source: Annotated[str, Field(min_length=1, max_length=160)]
    classification: Literal["descriptive", "causal_estimate", "qualitative", "creative_review"]
    payload: JsonObject
    evidence_sha256s: Annotated[tuple[Sha256Digest, ...], Field(min_length=1, max_length=128)]
    observed_at: datetime

    @model_validator(mode="after")
    def require_outcome_time(self) -> Self:
        _require_utc(self.observed_at)
        return self


class AgentLearning(ContractModel):
    schema_version: Literal["trace.agent-learning.v1"]
    learning_id: BoundedId
    run_id: BoundedId
    state: Literal["candidate", "approved", "rejected"]
    statement: Annotated[str, Field(min_length=1, max_length=4000)]
    outcome_sha256s: Annotated[tuple[Sha256Digest, ...], Field(min_length=1, max_length=128)]
    applicability: Annotated[str, Field(min_length=1, max_length=2000)]
    counter_evidence: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    reviewer_id: BoundedId | None = None
    created_at: datetime

    @model_validator(mode="after")
    def require_learning_review(self) -> Self:
        _require_utc(self.created_at)
        if (self.state == "candidate") == (self.reviewer_id is not None):
            message = "only reviewed learnings identify a reviewer"
            raise ValueError(message)
        return self


def contract_sha256(value: BaseModel | JsonObject) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return canonical_sha256(payload)


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        message = "agent contract time must be UTC"
        raise ValueError(message)


__all__ = [
    "AgentBudget",
    "AgentGoal",
    "AgentIntent",
    "AgentLearning",
    "AgentOutcome",
    "AgentRecord",
    "AgentRecordKind",
    "AgentRun",
    "AgentRunEvent",
    "AgentRunState",
    "AgentStep",
    "AgentStepKind",
    "CapabilitySnapshot",
    "ToolApproval",
    "ToolInvocation",
    "ToolReceiptRecord",
    "contract_sha256",
]
