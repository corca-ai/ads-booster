from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind, ToolReceiptRecord, contract_sha256
from ads_booster.contracts.task_progress import TaskCheckpoint

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RemainingBudget:
    tool_calls: int
    cost_units: int


@dataclass(frozen=True, slots=True)
class ProgressObservation:
    outcome_fingerprint: str
    evidence_sha256s: tuple[str, ...] = ()
    satisfied_obligation_ids: tuple[str, ...] = ()
    waiting: bool = False
    failure_description: str | None = None


def remaining_budget(run: AgentRun, records: tuple[AgentRecord, ...]) -> RemainingBudget:
    calls = sum(record.kind is AgentRecordKind.INVOCATION for record in records)
    cost = sum(
        ToolReceiptRecord.model_validate(record.payload).actual_cost_units
        for record in records
        if record.kind is AgentRecordKind.RECEIPT
    )
    return RemainingBudget(
        max(0, run.budget.max_tool_calls - calls), max(0, run.budget.max_cost_units - cost)
    )


def reserve_decision(
    checkpoint: TaskCheckpoint, *, assessment: bool = False
) -> TaskCheckpoint | None:
    final_slot_reserved = (
        not assessment and checkpoint.decision_calls + 1 >= checkpoint.policy.max_decision_calls
    )
    if (
        final_slot_reserved
        or checkpoint.decision_calls >= checkpoint.policy.max_decision_calls
        or (assessment and checkpoint.assessment_calls >= checkpoint.policy.max_assessments)
    ):
        return None
    return TaskCheckpoint.model_validate(
        {
            **checkpoint.model_dump(),
            "decision_calls": checkpoint.decision_calls + 1,
            "assessment_calls": checkpoint.assessment_calls + int(assessment),
        }
    )


def observe_progress(
    checkpoint: TaskCheckpoint, observation: ProgressObservation
) -> TaskCheckpoint:
    if observation.waiting:
        return checkpoint
    evidence = set(checkpoint.progress_evidence_sha256s)
    obligations = set(checkpoint.progress_obligation_ids)
    meaningful = bool(
        set(observation.evidence_sha256s) - evidence
        or set(observation.satisfied_obligation_ids) - obligations
    )
    fingerprints = (
        (observation.outcome_fingerprint,)
        if meaningful
        else (*checkpoint.fingerprints, observation.outcome_fingerprint)[-8:]
    )
    repeated = any(
        len(fingerprints) >= width * checkpoint.policy.no_progress
        and all(
            fingerprints[-width:] == fingerprints[-width * (index + 1) : -width * index or None]
            for index in range(checkpoint.policy.no_progress)
        )
        for width in (1, 2)
    )
    reconsidered = checkpoint.reconsideration_used and not meaningful
    blocked = repeated and reconsidered
    failure = observation.failure_description or observation.outcome_fingerprint
    return TaskCheckpoint.model_validate(
        {
            **checkpoint.model_dump(),
            "fingerprints": fingerprints,
            "progress_evidence_sha256s": tuple(
                sorted(evidence | set(observation.evidence_sha256s))
            ),
            "progress_obligation_ids": tuple(
                sorted(obligations | set(observation.satisfied_obligation_ids))
            ),
            "reconsideration_used": reconsidered or repeated,
            "strategy_feedback": (
                (
                    f"Repeated terminal failure: {failure}. "
                    "Reconsider the strategy once: use different supported evidence "
                    "or an alternate method; do not repeat the same failed operation."
                )
                if repeated and not reconsidered
                else None
                if meaningful
                else checkpoint.strategy_feedback
            ),
            "disposition": "blocked" if blocked else checkpoint.disposition,
            "wait_reason": "no_progress" if blocked else checkpoint.wait_reason,
            "next_action": "done" if blocked else checkpoint.next_action,
        }
    )


def outcome_fingerprint(capability_id: str, arguments: JsonObject, result: JsonObject) -> str:
    return contract_sha256(
        {
            "capability_id": capability_id,
            "arguments": arguments,
            "result": progress_evidence_fingerprint(result),
        }
    )


def progress_evidence_fingerprint(evidence: JsonObject) -> str:
    if evidence.get("schema_version") == "trace.tool-output-evidence.v1":
        return contract_sha256({"output": evidence["output"]})
    return contract_sha256(evidence)


def stable_failure_fingerprint(capability_id: str, evidence: JsonObject) -> str | None:
    output = evidence.get("output")
    if not isinstance(output, dict):
        return None
    error = output.get("error")
    code = error.get("code") if isinstance(error, dict) else error
    if not isinstance(code, str) or not code:
        return None
    return contract_sha256({"capability_id": capability_id, "failure_code": code})
