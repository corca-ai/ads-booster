from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentIntent,
    AgentRecordKind,
    CapabilitySnapshot,
    ToolApproval,
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.transport.json_types import JsonObject

_JSON: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)

if TYPE_CHECKING:
    from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.contracts.tool_capability import ToolDescriptor


@dataclass(frozen=True, slots=True)
class BoundCompletionEvidence:
    invocation: ToolInvocation
    descriptor: ToolDescriptor
    receipt: ToolReceiptRecord
    output: JsonObject
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class CompletionEvidenceReader:
    repository: SqliteAgentRunRepository

    def read(self, run: AgentRun, record: AgentRecord) -> BoundCompletionEvidence:
        records = self.repository.records(run.tenant_id, run.run_id)
        canonical = {item.payload_sha256: item for item in records}

        def payload(digest: str, kind: AgentRecordKind) -> JsonObject:
            item = canonical.get(digest)
            if item is None or item.kind is not kind or item.run_id != run.run_id:
                message = "completion_record_binding_invalid"
                raise ValueError(message)
            return item.payload

        if canonical.get(record.payload_sha256) != record or record.run_id != run.run_id:
            message = "completion_record_scope_invalid"
            raise ValueError(message)
        if record.payload_schema_version != "trace.tool-output-evidence.v1":
            message = "completion_output_schema_invalid"
            raise ValueError(message)
        receipt_digest = TypeAdapter(str).validate_python(record.payload["receipt_sha256"])
        receipt = ToolReceiptRecord.model_validate(payload(receipt_digest, AgentRecordKind.RECEIPT))
        output = _JSON.validate_python(record.payload["output"])
        invocation = ToolInvocation.model_validate(
            payload(receipt.invocation_sha256, AgentRecordKind.INVOCATION)
        )
        snapshot = CapabilitySnapshot.model_validate(
            payload(invocation.capability_snapshot_sha256, AgentRecordKind.CAPABILITY_SNAPSHOT)
        )
        intent = AgentIntent.model_validate(
            payload(invocation.intent_sha256, AgentRecordKind.INTENT)
        )
        descriptors = tuple(
            item
            for item in snapshot.descriptors
            if contract_sha256(item) == invocation.descriptor_sha256
        )
        if (
            invocation.run_id != run.run_id
            or invocation.tenant_id not in {None, run.tenant_id}
            or snapshot.run_id != run.run_id
            or intent.run_id != run.run_id
            or len(descriptors) != 1
            or contract_sha256(output) != receipt.output_sha256
        ):
            message = "completion_tool_binding_invalid"
            raise ValueError(message)
        descriptor = descriptors[0]
        if (
            descriptor.capability_id != intent.capability_id
            or descriptor.capability_id != record.payload.get("capability_id")
            or contract_sha256(descriptor.output_schema) != receipt.output_schema_sha256
        ):
            message = "completion_descriptor_binding_invalid"
            raise ValueError(message)
        if descriptor.approval_policy.mode == "required":
            if receipt.approval_sha256 is None:
                message = "completion_approval_missing"
                raise ValueError(message)
            approval = ToolApproval.model_validate(
                payload(receipt.approval_sha256, AgentRecordKind.APPROVAL)
            )
            if (
                approval.invocation_sha256 != receipt.invocation_sha256
                or approval.decision != "granted"
                or approval.decided_at > receipt.occurred_at
            ):
                message = "completion_approval_binding_invalid"
                raise ValueError(message)
        return BoundCompletionEvidence(
            invocation, descriptor, receipt, output, record.payload_sha256
        )
