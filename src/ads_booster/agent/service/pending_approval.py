"""Replay the unexecuted proposal independently of conversational/read-tool turns."""

from __future__ import annotations

from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    CapabilitySnapshot,
    ToolInvocation,
    contract_sha256,
)


def pending_approval(records: tuple[AgentRecord, ...]) -> ToolInvocation | None:
    """Only canonical decisions, invocations and approvals may change the proposal.

    Tool observations and nested source text never cancel or approve work. A read
    invocation is deliberately not the new approval target. Historical decisions
    without a disposition preserve the proposal by default.
    """
    required: set[str] = set()
    pending: ToolInvocation | None = None
    for record in records:
        if record.kind is AgentRecordKind.CAPABILITY_SNAPSHOT:
            snapshot = CapabilitySnapshot.model_validate(record.payload)
            required.update(
                contract_sha256(d)
                for d in snapshot.descriptors
                if d.approval_policy.mode == "required"
            )
        elif record.kind is AgentRecordKind.REASONING:
            decision = record.payload.get("decision")
            if isinstance(decision, dict) and decision.get("pending_approval_action") in {
                "replace",
                "cancel",
            }:
                pending = None
        elif record.kind is AgentRecordKind.EVIDENCE and (
            record.payload_schema_version == "trace.work-cancelled.v1"
            or (
                record.payload_schema_version == "trace.work-continuation.v1"
                and record.payload.get("action") == "pause"
            )
        ):
            pending = None
        elif record.kind is AgentRecordKind.INVOCATION:
            invocation = ToolInvocation.model_validate(record.payload)
            if invocation.descriptor_sha256 in required:
                pending = invocation
        elif (
            pending is not None
            and record.kind in {AgentRecordKind.APPROVAL, AgentRecordKind.RECEIPT}
            and record.payload.get("invocation_sha256") == contract_sha256(pending)
        ):
            pending = None
    return pending
