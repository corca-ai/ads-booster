"""Projection of host-admitted task input, independent of observation compaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun


def current_user_message(run: AgentRun, records: tuple[AgentRecord, ...]) -> str:
    """Project admitted user intent independently of bounded evidence compaction.

    Only the continuation owner's direct ledger records qualify. Nested tool
    output, submitted evidence and historical assistant text cannot set intent.
    The message can steer the task, but cannot authorize an external effect.
    """
    for record in reversed(records):
        payload = record.payload
        event_id, note = payload.get("event_id"), payload.get("note")
        if (
            record.kind is not AgentRecordKind.EVIDENCE
            or record.payload_schema_version != "trace.work-continuation.v1"
            or not isinstance(event_id, str)
            or not isinstance(note, str)
            or payload.get("action") != "revise"
            or record.record_id
            != f"{run.run_id}:continuation:{contract_sha256({'event_id': event_id})[:32]}"
        ):
            continue
        return note
    return run.goal.objective
