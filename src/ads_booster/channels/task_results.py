from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.agent.service.deferred_failure import FAILURE_TEXT
from ads_booster.agent.service.task_progress import project_task
from ads_booster.contracts.agent_run import (
    AgentIntent,
    AgentRecordKind,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.task_completion import CompletionAssessment, CompletionCandidate
from ads_booster.contracts.task_progress import TaskCheckpoint
from ads_booster.contracts.trace_post import TracePostSuccess
from ads_booster.threads.drafts import ThreadsDraftBatch
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.agent.core.ports import CompletionRenderContext
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun


class DeliveryIdentity(ContractModel):
    tenant_id: str
    run_id: str
    task_id: str
    task_revision: int
    candidate_sha256: str
    assessment_id: str
    answer_sha256: str


@dataclass(frozen=True, slots=True)
class TaskResult:
    text: str
    task_disposition: str
    identity: DeliveryIdentity | None = None


def latest_invocation(records: tuple[AgentRecord, ...]) -> ToolInvocation | None:
    latest = next(
        (record for record in reversed(records) if record.kind is AgentRecordKind.INVOCATION),
        None,
    )
    return None if latest is None else ToolInvocation.model_validate(latest.payload)


@dataclass(frozen=True, slots=True)
class BoundedCompletionRenderer:
    max_chars: int = 3500
    include_links: bool = True

    def render(
        self, candidate: CompletionCandidate, context: CompletionRenderContext | None = None
    ) -> CompletionCandidate:
        links = candidate.result_links if self.include_links else ()
        additions = tuple(link for link in links if link not in candidate.answer)
        body = "\n".join((candidate.answer, *additions))
        if len(body) > self.max_chars:
            body = body[: self.max_chars - 1] + "…"
        attachments = candidate.attachment_refs if self.include_links else ()
        if context is not None and self.include_links:
            receipts = {
                record.payload_sha256: record.payload
                for record in context.records
                if record.kind is AgentRecordKind.RECEIPT
            }
            references: list[str] = []
            for record in context.records:
                if (
                    record.run_id != context.run.run_id
                    or record.payload_sha256 not in candidate.evidence_sha256s
                ):
                    continue
                output = record.payload.get("output")
                receipt = receipts.get(str(record.payload.get("receipt_sha256")))
                if (
                    isinstance(output, dict)
                    and receipt is not None
                    and receipt.get("disposition") == "succeeded"
                    and receipt.get("output_sha256") == contract_sha256(output)
                ):
                    references.extend(_artifact_digests(output))
            attachments = tuple(dict.fromkeys(references))
        return CompletionCandidate.model_validate(
            {
                **candidate.model_dump(),
                "answer": body,
                "answer_sha256": contract_sha256({"answer": body}),
                "result_links": links,
                "attachment_refs": attachments,
            }
        )


def result_for(run: AgentRun, records: tuple[AgentRecord, ...]) -> TaskResult:
    task = project_task(run, records)
    candidate = task.checkpoint.candidate
    if task.checkpoint.disposition == "satisfied" and candidate is not None:
        for record in reversed(records):
            if record.payload_schema_version != "trace.task-completion.v1":
                continue
            assessment = CompletionAssessment.model_validate(record.payload)
            if (
                assessment.task_id,
                assessment.task_revision,
                assessment.task_spec_sha256,
                assessment.candidate_sha256,
                assessment.disposition,
            ) == (
                task.spec.task_id,
                task.spec.task_revision,
                contract_sha256(task.spec),
                contract_sha256(candidate),
                "satisfied",
            ):
                return TaskResult(
                    candidate.answer,
                    "satisfied",
                    DeliveryIdentity(
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        task_id=task.spec.task_id,
                        task_revision=task.spec.task_revision,
                        candidate_sha256=contract_sha256(candidate),
                        assessment_id=assessment.assessment_id,
                        answer_sha256=candidate.answer_sha256,
                    ),
                )
    if run.state.value in {"created", "running"}:
        return TaskResult("작업을 계속 진행하고 있습니다.", task.checkpoint.disposition)
    if run.state.value == "awaiting_tool":
        return TaskResult(
            "요청한 작업을 실행 중입니다. 결과가 준비되면 이 대화에 전달합니다.", "waiting"
        )
    if run.state.value == "awaiting_input" and (question := current_input_question(run, records)):
        return TaskResult(question, task.checkpoint.disposition)
    if run.state.value == "awaiting_reconciliation":
        return _reconciliation_result(run, records, task.checkpoint.disposition)
    verified = {item.obligation_id for item in task.checkpoint.accepted_evidence}
    finished = [
        item.description for item in task.spec.obligations if item.obligation_id in verified
    ]
    pending = [
        item.description
        for item in task.spec.obligations
        if item.required and item.obligation_id not in verified
    ]
    lines = ["작업을 완료하지 못했습니다."]
    if finished:
        lines.append("확인된 작업: " + "; ".join(finished)[:1000])
    if pending:
        lines.append("남은 작업: " + "; ".join(pending)[:1600])
    lines.append("사유: " + (task.checkpoint.wait_reason or run.blocked_reason or run.state.value))
    return TaskResult("\n".join(lines), task.checkpoint.disposition)


def _reconciliation_result(
    run: AgentRun, records: tuple[AgentRecord, ...], disposition: str
) -> TaskResult:
    publication = next(
        (
            record.payload.get("output")
            for record in reversed(records)
            if record.kind is AgentRecordKind.EVIDENCE
            and record.payload.get("capability_id") in {"threads.publish", "threads.reply"}
            and isinstance(record.payload.get("output"), dict)
        ),
        None,
    )
    if isinstance(publication, dict):
        receipts = publication.get("publications")
        identities = (
            []
            if not isinstance(receipts, list)
            else [
                f"{item.get('operation_id')} / {item.get('published_post_id')}"
                for item in receipts
                if isinstance(item, dict)
            ]
        )
        message = (
            "Threads 결과를 확정하지 못했습니다. 자동으로 다시 게시하지 않습니다.\n"
            + "\n".join(identities)
        )
        return TaskResult(
            message,
            disposition,
        )
    diagnostic = next(
        (
            record.payload.get("reason_code")
            for record in reversed(records)
            if record.run_id == run.run_id
            and record.payload_schema_version == "trace.deferred-provider-failure.v1"
        ),
        None,
    )
    reason = (
        FAILURE_TEXT[diagnostic]
        if isinstance(diagnostic, str) and diagnostic in FAILURE_TEXT
        else "요청한 작업의 실행 결과를 확인하지 못했습니다."
    )
    return TaskResult(
        reason + " 결과가 확인되지 않아 자동으로 다시 실행하지 않았습니다.",
        disposition,
    )


def current_input_question(run: AgentRun, records: tuple[AgentRecord, ...]) -> str | None:
    task = project_task(run, records)
    latest = next(
        (
            r
            for r in reversed(records)
            if r.run_id == run.run_id and r.kind is AgentRecordKind.INTENT
        ),
        None,
    )
    if latest is None:
        return None
    intent = AgentIntent.model_validate(latest.payload)
    if intent.action != "request_input" or intent.run_id != run.run_id:
        return None
    revision = intent.step_id.removeprefix(f"{run.run_id}:step:")
    for record in reversed(records):
        if (
            record.run_id == run.run_id
            and record.payload_schema_version == "trace.task-checkpoint.v1"
            and record.record_id == f"task:{record.payload_sha256}:{revision}"
        ):
            checkpoint = TaskCheckpoint.model_validate(record.payload)
            if (
                checkpoint.task_id,
                checkpoint.task_revision,
                checkpoint.spec_sha256,
                checkpoint.next_action,
            ) == (task.spec.task_id, task.spec.task_revision, contract_sha256(task.spec), "wait"):
                return intent.reasoning_summary
            return None
    return None


def attachment_records(run: AgentRun, records: tuple[AgentRecord, ...]) -> tuple[AgentRecord, ...]:
    if result_for(run, records).identity is None:
        return ()
    candidate = project_task(run, records).checkpoint.candidate
    if candidate is None:
        return ()
    selected: list[AgentRecord] = []
    for record in records:
        if record.kind is AgentRecordKind.RECEIPT or (
            record.kind is AgentRecordKind.EVIDENCE
            and _selected_attachment_evidence(record, candidate)
        ):
            selected.append(record)
    return tuple(selected)


def _selected_attachment_evidence(record: AgentRecord, candidate: CompletionCandidate) -> bool:
    if record.payload_sha256 not in candidate.evidence_sha256s:
        return False
    output = record.payload.get("output")
    if not isinstance(output, dict):
        return False
    digests = _artifact_digests(output)
    return bool(digests) and set(digests).issubset(candidate.attachment_refs)


def _artifact_digests(output: JsonObject) -> tuple[str, ...]:
    digest = output.get("artifact_sha256")
    if isinstance(digest, str):
        return (digest,)
    batch_payload = output.get("batch")
    if isinstance(batch_payload, dict):
        batch = ThreadsDraftBatch.model_validate(batch_payload)
        return tuple(
            asset.sha256 for item in batch.items if not item.excluded for asset in item.assets
        )
    if output.get("schema_version") != "trace.trace-post-success.v1":
        return ()
    result = TracePostSuccess.model_validate(output)
    return tuple(item.asset.sha256 for item in result.assets)
