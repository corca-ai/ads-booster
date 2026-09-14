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


def new_input_after_brand_wait(records: tuple[AgentRecord, ...]) -> bool:
    """A newly admitted input can reclassify a previous brand-blocked request."""
    input_index = next(
        (
            index
            for index in range(len(records) - 1, -1, -1)
            if records[index].payload_schema_version == "trace.agent-input-evidence.v1"
        ),
        None,
    )
    if input_index is None or any(
        record.kind is AgentRecordKind.REASONING for record in records[input_index + 1 :]
    ):
        return False
    for record in reversed(records[:input_index]):
        if record.kind is AgentRecordKind.REASONING:
            return False
        if record.payload_schema_version != "trace.knowledge-preparation-blocked.v1":
            continue
        preparation = record.payload.get("preparation")
        return isinstance(preparation, dict) and (
            preparation.get("status") == "brand_unresolved"
            or preparation.get("error_code") in {"required_voice_unavailable", "scope_unresolved"}
        )
    return False
