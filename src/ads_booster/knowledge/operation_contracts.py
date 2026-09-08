from __future__ import annotations

# ruff: noqa: EM101, TC001
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import (
    BoundedReason,
    BoundedText,
    KnowledgeContractModel,
    UtcDatetime,
)
from ads_booster.knowledge.operation_enums import (
    BatchState,
    ChangeImpactKind,
    CorrectionScope,
    CorrectionStatus,
    CurationTarget,
    JobKind,
    JobPriority,
    JobState,
    KnowledgeOperationKind,
    MemoryOperationKind,
    OperationStatus,
)
from ads_booster.knowledge.scope_contracts import AccessScope


class SemanticFingerprint(KnowledgeContractModel):
    claim_id: BoundedId
    statement_sha256: Sha256Digest
    evidence_sha256: Sha256Digest
    applicability_sha256: Sha256Digest
    combined_sha256: Sha256Digest


class ChangeImpact(KnowledgeContractModel):
    kind: ChangeImpactKind
    previous_fingerprint: SemanticFingerprint | None = None
    current_fingerprint: SemanticFingerprint | None = None
    affected_dependency_ids: Annotated[tuple[BoundedId, ...], Field(max_length=512)] = ()
    reason: BoundedReason


class KnowledgeOperation(KnowledgeContractModel):
    operation_id: BoundedId
    kind: KnowledgeOperationKind
    target_page_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=128)]
    expected_revision_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=128)]
    claim_ids: Annotated[tuple[BoundedId, ...], Field(max_length=256)] = ()
    evidence_refs: Annotated[tuple[BoundedId, ...], Field(max_length=256)] = ()
    reason: BoundedReason


class MemoryOperation(KnowledgeContractModel):
    operation_id: BoundedId
    kind: MemoryOperationKind
    document_id: BoundedId
    entry_id: BoundedId
    expected_revision_id: BoundedId
    replacement_entry_id: BoundedId | None = None
    reason: BoundedReason
    evidence_refs: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=128)]


class CorrectionIntent(KnowledgeContractModel):
    schema_version: Literal["knowledge.correction-intent.v1"] = Field(alias="schema")
    intent_id: BoundedId
    request_id: BoundedId
    actor_ref: BoundedId
    workspace_id: BoundedId
    scope: CorrectionScope
    task_ref: BoundedId | None = None
    brand_ref: BoundedId | None = None
    target_ids: Annotated[tuple[BoundedId, ...], Field(min_length=1, max_length=128)]
    correction_text: BoundedText
    authenticated_event_ref: BoundedId
    status: CorrectionStatus
    policy_epoch: Annotated[int, Field(ge=1)]
    created_at: UtcDatetime

    @model_validator(mode="after")
    def require_scope_binding(self) -> Self:
        if self.scope is CorrectionScope.TASK_ONLY and self.task_ref is None:
            raise PydanticCustomError(
                "task_correction_requires_task",
                "task-only corrections require a trusted task reference",
            )
        if self.scope is CorrectionScope.TEAM and self.task_ref is not None:
            raise PydanticCustomError(
                "team_correction_forbids_task",
                "team corrections cannot use a task binding",
            )
        return self


class MemoryExplanation(KnowledgeContractModel):
    explanation_id: BoundedId
    target_id: BoundedId
    admission_reason: BoundedReason | None = None
    evidence_refs: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    applicability: Annotated[str, Field(max_length=2_000)] = ""
    selected_reason: BoundedReason | None = None
    unknown_reason: bool = False


class KnowledgeJob(KnowledgeContractModel):
    schema_version: Literal["knowledge.job.v1"] = Field(alias="schema")
    job_id: BoundedId
    workspace_id: BoundedId
    scope: AccessScope
    kind: JobKind
    state: JobState
    priority: JobPriority
    root_event_id: BoundedId
    policy_version: BoundedId
    due_at: UtcDatetime
    created_at: UtcDatetime
    batch_id: BoundedId | None = None
    reason_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    lease_generation: Annotated[int, Field(ge=0)] = 0


class EventReceipt(KnowledgeContractModel):
    event_id: BoundedId
    event_revision: Annotated[int, Field(ge=1)]
    status: OperationStatus
    targets: Annotated[tuple[CurationTarget, ...], Field(max_length=8)] = ()
    reason: BoundedReason | None = None

    @model_validator(mode="after")
    def require_unique_targets(self) -> Self:
        if len(self.targets) != len(set(self.targets)):
            raise PydanticCustomError(
                "curation_targets_must_be_unique",
                "curation targets must be unique",
            )
        return self


class CurationBatch(KnowledgeContractModel):
    schema_version: Literal["knowledge.curation-batch.v1"] = Field(alias="schema")
    batch_id: BoundedId
    workspace_id: BoundedId
    scope: AccessScope
    priority: JobPriority
    policy_version: BoundedId
    read_grant_sha256: Sha256Digest
    write_capability_sha256: Sha256Digest
    state: BatchState = BatchState.COLLECTING
    first_event_at: UtcDatetime
    batch_deadline: UtcDatetime
    event_receipts: Annotated[tuple[EventReceipt, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def require_batch_window(self) -> Self:
        if self.batch_deadline < self.first_event_at:
            raise PydanticCustomError(
                "batch_deadline_precedes_first_event",
                "batch deadline cannot precede the first event",
            )
        return self


class OperationReceipt(KnowledgeContractModel):
    schema_version: Literal["knowledge.operation-receipt.v1"] = Field(alias="schema")
    operation_id: BoundedId
    status: OperationStatus
    resulting_revision_ids: Annotated[tuple[BoundedId, ...], Field(max_length=256)] = ()
    error_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    retryable: bool
    occurred_at: UtcDatetime
