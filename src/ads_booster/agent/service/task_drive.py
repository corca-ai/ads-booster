from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.agent.core.ports import ReasoningProviderV2
from ads_booster.agent.service.task_progress import TaskProjection, apply_proposal
from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningDecisionV2,
    ReasoningRequestV2,
)
from ads_booster.contracts.task_completion import CompletionAssessment, CompletionCandidate

if TYPE_CHECKING:
    from ads_booster.agent.core.ports import ReasoningProvider
    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult, ReasoningResultV2
    from ads_booster.contracts.task_progress import TaskSpec
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class DecisionProjectionContext:
    run: AgentRun
    selected_evidence: tuple[BoundCompletionEvidence, ...] = ()


def plan_task(
    provider: ReasoningProvider | ReasoningProviderV2,
    request: ReasoningRequest,
    task: TaskProjection,
    records: tuple[AgentRecord, ...],
) -> ReasoningResult | ReasoningResultV2:
    match provider:
        case ReasoningProviderV2():
            feedback = next(
                (
                    CompletionAssessment.model_validate(item.payload)
                    for item in reversed(records)
                    if item.payload_schema_version == "trace.task-completion.v1"
                    and item.payload.get("task_id") == task.spec.task_id
                    and item.payload.get("task_revision") == task.spec.task_revision
                ),
                None,
            )
            submitted = ReasoningRequestV2.model_validate(
                {
                    **request.model_dump(),
                    "schema_version": "trace.reasoning-request.v2",
                    "task": task.spec,
                    "checkpoint": task.checkpoint,
                    "completion_feedback": feedback,
                    "evidence": evidence_with_handles(request, records),
                }
            )
            result = provider.plan_v2(submitted)
            digest = contract_sha256(submitted)
        case _:
            result = provider.plan(request)
            digest = contract_sha256(request)
    if result.receipt.request_sha256 != digest:
        message = "reasoning_receipt_request_digest_mismatch"
        raise ValueError(message)
    return result


def evidence_with_handles(
    request: ReasoningRequest, records: tuple[AgentRecord, ...]
) -> tuple[JsonObject, ...]:
    admissible = {
        record.payload_sha256
        for record in records
        if record.run_id == request.run_id
        and record.kind is AgentRecordKind.EVIDENCE
        and record.payload_schema_version == "trace.tool-output-evidence.v1"
    }
    projected: list[JsonObject] = []
    for evidence in request.evidence:
        item = dict(evidence)
        _ = item.pop("host_evidence_sha256", None)
        digest = contract_sha256(evidence)
        if digest in admissible:
            item["host_evidence_sha256"] = digest
        projected.append(item)
    return tuple(projected)


def project_decision(
    task: TaskProjection,
    decision: ReasoningDecision | ReasoningDecisionV2,
    context: DecisionProjectionContext,
) -> TaskProjection:
    spec = task.spec
    match decision:
        case ReasoningDecisionV2():
            if decision.task_proposal is not None:
                spec = apply_proposal(spec, decision.task_proposal)
            candidate = decision.completion_candidate
        case ReasoningDecision():
            candidate = _legacy_candidate(spec, decision, context)
    return TaskProjection(
        spec,
        task.checkpoint.model_copy(
            update={
                "spec_sha256": contract_sha256(spec),
                "unresolved_obligation_ids": tuple(
                    item.obligation_id
                    for item in spec.obligations
                    if item.required
                    and item.obligation_id
                    not in {
                        accepted.obligation_id for accepted in task.checkpoint.accepted_evidence
                    }
                ),
                "candidate": candidate,
                "next_action": {
                    "stop": "assess",
                    "request_input": "wait",
                    "invoke_tool": "execute",
                }[decision.action],
            }
        ),
    )


def _legacy_candidate(
    spec: TaskSpec,
    decision: ReasoningDecision,
    context: DecisionProjectionContext,
) -> CompletionCandidate | None:
    if decision.action != "stop":
        return None
    deliverables = tuple(
        item
        for item in context.selected_evidence
        if item.receipt.disposition == "succeeded"
        and (
            isinstance(item.output.get("url"), str)
            or isinstance(item.output.get("artifact_sha256"), str)
        )
    )
    links = tuple(
        dict.fromkeys(
            link
            for item in deliverables
            if isinstance((link := item.output.get("url")), str)
        )
    )
    attachments = tuple(
        dict.fromkeys(
            digest
            for item in deliverables
            if isinstance((digest := item.output.get("artifact_sha256")), str)
        )
    )
    return CompletionCandidate(
        candidate_id=f"candidate:{context.run.run_id}:{context.run.revision}",
        task_id=spec.task_id,
        task_revision=spec.task_revision,
        answer=decision.reasoning_summary,
        answer_sha256=contract_sha256({"answer": decision.reasoning_summary}),
        result_links=links,
        attachment_refs=attachments,
        evidence_sha256s=tuple(dict.fromkeys(item.evidence_sha256 for item in deliverables)),
    )
