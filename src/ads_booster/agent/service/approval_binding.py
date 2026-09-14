from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.agent.runtime import ApprovalGrant
from ads_booster.contracts.agent_run import AgentRecordKind, ToolApproval, contract_sha256

if TYPE_CHECKING:
    from ads_booster.agent.runtime import AgentSession
    from ads_booster.contracts.agent_run import AgentRecord, ToolInvocation


def runtime_grant_approval(
    records: tuple[AgentRecord, ...], invocation: ToolInvocation, session: AgentSession
) -> ToolApproval | None:
    """Resolve the canonical approval bound to the runtime's pending grant."""
    if session.pending_call is None:
        return None
    invocation_sha256 = contract_sha256(invocation)
    for record in records:
        if record.kind is not AgentRecordKind.APPROVAL:
            continue
        approval = ToolApproval.model_validate(record.payload)
        if (
            approval.invocation_sha256 == invocation_sha256
            and approval.decision == "granted"
            and approval.expires_at is not None
        ):
            grant = ApprovalGrant(
                approval.approval_id,
                session.pending_call.digest,
                approval.approver_id,
                approval.expires_at,
            )
            if grant.digest == session.pending_grant_sha256:
                return approval
    return None
