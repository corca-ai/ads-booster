from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter

from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    ToolReceiptRecord,
)
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.threads.accounts import ThreadsAccountConflictError
from ads_booster.threads.publications import ThreadsPublicationRepository, ThreadsPublisher
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_ITEM_IDS: TypeAdapter[list[str]] = TypeAdapter(list[str])


@dataclass(frozen=True, slots=True)
class ThreadsReconciliationRuntime:
    service: MarketingAgentService
    publisher: ThreadsPublisher
    publications: ThreadsPublicationRepository

    def work_once(self, *, now: datetime) -> bool:
        for receipt in self.publications.reconciliation_candidates():
            run = self.service.repository.get(receipt.workspace_id, receipt.run_id)
            if run is None:
                continue
            if run.state is not AgentRunState.AWAITING_RECONCILIATION:
                settled = any(
                    record.kind is AgentRecordKind.RECEIPT
                    and ToolReceiptRecord.model_validate(record.payload).invocation_sha256
                    == receipt.invocation_sha256
                    for record in self.service.repository.records(
                        receipt.workspace_id, receipt.run_id
                    )
                )
                if settled:
                    self.publications.mark_run_settled(receipt.operation_id)
                continue
            if receipt.state == "uncertain" and receipt.published_post_id is None:
                continue
            try:
                resolved = self.publisher.reconcile(
                    operation_id=receipt.operation_id,
                    workspace_id=receipt.workspace_id,
                    member_id=receipt.owner_member_id,
                    now=now,
                )
            except ThreadsAccountConflictError:
                continue
            if resolved.state != "published":
                continue
            invocation = next(
                (
                    ToolInvocation.model_validate(record.payload)
                    for record in self.service.repository.records(
                        receipt.workspace_id, receipt.run_id
                    )
                    if record.kind is AgentRecordKind.INVOCATION
                    and record.payload_sha256 == receipt.invocation_sha256
                ),
                None,
            )
            if invocation is None:
                continue
            requested_value = invocation.input.get("item_ids")
            if not isinstance(requested_value, list):
                continue
            requested = _ITEM_IDS.validate_python(requested_value)
            group = self.publications.for_invocation(receipt.invocation_sha256)
            attempted = {item.item_id for item in group}
            unattempted = [item for item in requested if item not in attempted]
            output = _JSON_OBJECT.validate_python(
                {
                    "publications": [item.model_dump(mode="json") for item in group],
                    "requested_item_ids": requested,
                    "unattempted_item_ids": unattempted,
                }
            )
            disposition = (
                "succeeded"
                if not unattempted and all(item.state == "published" for item in group)
                else "failed"
            )
            reconciled_run = self.service.resolve_reconciliation(
                receipt.workspace_id,
                receipt.run_id,
                result=ToolExecutionResult(
                    schema_version="trace.tool-execution-result.v1",
                    disposition=disposition,
                    invocation_sha256=receipt.invocation_sha256,
                    output=output,
                    actual_cost_units=0,
                    executor_id="threads-api",
                ),
                now=now,
            )
            _ = self.service.drive(
                reconciled_run.tenant_id, reconciled_run.run_id, now=now
            )
            for item in group:
                self.publications.mark_run_settled(item.operation_id)
            return True
        return False


__all__ = ["ThreadsReconciliationRuntime"]
