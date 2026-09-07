"""Identity-bound human continuation at a safe canonical Run boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    contract_sha256,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.transport.json_types import JsonObject


_MAX_NOTE_CHARS = 20_000


def continue_work(  # noqa: PLR0913 - authenticated identity and idempotency are explicit.
    service: MarketingAgentService,
    tenant_id: str,
    run_id: str,
    *,
    event_id: str,
    actor_id: str,
    note: str,
    action: Literal["revise", "pause"],
    now: datetime,
    inputs: JsonObject | None = None,
) -> AgentRun:
    """Preserve human reports without converting them into verification or grants.

    The channel authenticates actor and tenant before calling. This owner performs
    Run-scoped admission under the same execution lock as tool dispatch. A pause
    waits for a human continuation; it never means an external effect was undone.
    """
    if not event_id or not actor_id or not note.strip() or len(note) > _MAX_NOTE_CHARS:
        raise ValueError("work_continuation_invalid")
    if action not in {"revise", "pause"}:
        raise ValueError("work_continuation_action_invalid")
    payload: JsonObject = {
        "schema_version": "trace.work-continuation.v1",
        "event_id": event_id,
        "actor_id": actor_id,
        "action": action,
        "note": note,
        "verification": "human_reported",
        "authority": "task_input_only",
        "inputs": inputs or {},
    }
    digest = contract_sha256(payload)
    record_id = f"{run_id}:continuation:{contract_sha256({'event_id': event_id})[:32]}"
    with service.execution_lock:
        run = service.repository.get(tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        existing = next(
            (r for r in service.repository.records(tenant_id, run_id) if r.record_id == record_id),
            None,
        )
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise ValueError("work_continuation_idempotency_conflict")
            return _resume_continuation(service, run, payload, now=now)
        # Do not obscure interrupted dispatch or unknown effects with new work.
        if run.state in {
            AgentRunState.RUNNING,
            AgentRunState.CREATED,
            AgentRunState.AWAITING_RECONCILIATION,
            AgentRunState.BLOCKED,
        }:
            raise ValueError("work_continuation_requires_safe_boundary")
        updated = service.repository.append_step(
            run,
            AgentStep(
                schema_version="trace.agent-step.v1",
                step_id=f"{run_id}:step:{run.revision}",
                run_id=run_id,
                sequence=run.revision,
                kind=AgentStepKind.OBSERVE,
                state="completed",
                input_sha256=digest,
                output_sha256=digest,
                parent_step_sha256=run.head_step_sha256,
                occurred_at=now,
            ),
            state=AgentRunState.AWAITING_INPUT,
            expected_revision=run.revision,
            records=(
                AgentRecord(
                    schema_version="trace.agent-record.v1",
                    record_id=record_id,
                    run_id=run_id,
                    kind=AgentRecordKind.EVIDENCE,
                    payload_schema_version="trace.work-continuation.v1",
                    payload=payload,
                    payload_sha256=digest,
                    occurred_at=now,
                ),
            ),
        )
        if service.fault_hook is not None:
            service.fault_hook("work_continuation_committed")
        if action == "pause":
            return updated
        return service.submit_input(tenant_id, run_id, payload, now=now)


def _resume_continuation(
    service: MarketingAgentService,
    run: AgentRun,
    payload: JsonObject,
    *,
    now: datetime,
) -> AgentRun:
    """Resume only this event's partial admission, never a later human wait.

    Canonical input and continuation records identify the owning human event.
    drive retains the runtime's dispatch/reconciliation behavior for RUNNING work.
    """
    if payload["action"] != "revise":
        return run
    records = service.repository.records(run.tenant_id, run.run_id)
    latest_input = next(
        (
            record
            for record in reversed(records)
            if record.payload_schema_version
            in {
                "trace.work-continuation.v1",
                "trace.work-interruption.v1",
                "trace.agent-input-evidence.v1",
            }
        ),
        None,
    )
    if latest_input is None:
        return run
    if run.state is AgentRunState.AWAITING_INPUT and latest_input.payload == payload:
        steps = service.repository.steps(run.tenant_id, run.run_id)
        digest = contract_sha256(payload)
        session = service.runtime_store.load(run.run_id)
        if (
            steps
            and steps[-1].kind is AgentStepKind.OBSERVE
            and steps[-1].input_sha256 == digest
            and steps[-1].output_sha256 == digest
            and (session is None or session.pending_invocation is None)
        ):
            return service.submit_input(run.tenant_id, run.run_id, payload, now=now)
    if (
        run.state is AgentRunState.RUNNING
        and latest_input.payload_schema_version == "trace.agent-input-evidence.v1"
        and latest_input.payload.get("evidence") == payload
    ):
        return service.drive(run.tenant_id, run.run_id, now=now)
    return run
