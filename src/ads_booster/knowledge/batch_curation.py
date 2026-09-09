from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import CurationBatch, EventReceipt
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.identifiers import stable_id
from ads_booster.knowledge.learning_policy import LEARNING_POLICY_VERSION
from ads_booster.knowledge.operation_enums import (
    BatchState,
    JobPriority,
    OperationStatus,
)
from ads_booster.knowledge.repository_batch import (
    BatchItemWrite,
    CollectingBatchQuery,
    collect_curation_item,
    collecting_curation_batch,
    curation_batch_generation,
    finish_curation_batch,
    ready_curation_batch,
)
from ads_booster.knowledge.repository_types import conflict

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.curation_contracts import CurationRequest
    from ads_booster.knowledge.curation_runtime import CancellationSignal
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


@dataclass(frozen=True, slots=True)
class CurationBatchPolicy:
    collection_seconds: int = 60


@dataclass(frozen=True, slots=True)
class CurationBatchItem:
    job_id: str
    event_id: str
    event_revision: int
    actor: ActorContext
    policy_version: str
    priority: JobPriority
    occurred_at: datetime


def curation_partition_key(item: CurationBatchItem) -> str:
    """Bind a batch partition to its actor, grants, scope, policy, and epoch."""
    read_grant = authorize_read(
        actor=item.actor,
        target_scope=item.actor.conversation_scope,
        at=item.occurred_at,
    )
    write_grant = authorize_write(
        actor=item.actor,
        target_scope=item.actor.conversation_scope,
        at=item.occurred_at,
    )
    return contract_sha256(
        {
            "workspace_id": item.actor.workspace_id,
            "scope": item.actor.conversation_scope.model_dump(mode="json"),
            "member_id": item.actor.member_id,
            "session_id": item.actor.session_id,
            "read_grant": contract_sha256(read_grant),
            "write_grant": contract_sha256(write_grant),
            "policy_version": item.policy_version,
            "policy_epoch": item.actor.policy_epoch,
        }
    )


@dataclass(frozen=True, slots=True)
class CurationBatchWork:
    request: CurationRequest
    trusted_context: TrustedInvocationContext


@dataclass(frozen=True, slots=True)
class ClaimedBatchRun:
    actor: ActorContext
    batch: CurationBatch
    items: tuple[CurationBatchWork, ...]
    cancellation: CancellationSignal | None = None


class CurationBatchProcessor(Protocol):
    def process(self, run: ClaimedBatchRun) -> tuple[EventReceipt, ...]: ...


@dataclass(frozen=True, slots=True)
class BatchCurationCoordinator:
    repository: SqliteKnowledgeRepository
    policy: CurationBatchPolicy = CurationBatchPolicy()

    def collect(self, item: CurationBatchItem) -> CurationBatch:
        read_grant = authorize_read(
            actor=item.actor,
            target_scope=item.actor.conversation_scope,
            at=item.occurred_at,
        )
        write_grant = authorize_write(
            actor=item.actor,
            target_scope=item.actor.conversation_scope,
            at=item.occurred_at,
        )
        read_grant_sha256 = contract_sha256(read_grant)
        write_capability_sha256 = contract_sha256(write_grant)
        isolation_key = curation_partition_key(item)
        stored_partition_key = (
            isolation_key if item.policy_version == LEARNING_POLICY_VERSION else None
        )
        existing = collecting_curation_batch(
            self.repository,
            item.actor,
            CollectingBatchQuery(
                priority=item.priority,
                policy_version=item.policy_version,
                read_grant_sha256=read_grant_sha256,
                write_capability_sha256=write_capability_sha256,
                isolation_key=stored_partition_key,
            ),
        )
        if existing is None:
            generation = curation_batch_generation(self.repository, item.actor, item.event_id)
            batch_id = stable_id("curation-batch", isolation_key, item.event_id, str(generation))
            deadline = (
                item.occurred_at
                if item.priority is JobPriority.URGENT
                else item.occurred_at + timedelta(seconds=self.policy.collection_seconds)
            )
            batch = CurationBatch(
                schema="knowledge.curation-batch.v1",
                batch_id=batch_id,
                workspace_id=item.actor.workspace_id,
                scope=item.actor.conversation_scope,
                priority=item.priority,
                policy_version=item.policy_version,
                read_grant_sha256=read_grant_sha256,
                write_capability_sha256=write_capability_sha256,
                state=BatchState.COLLECTING,
                first_event_at=item.occurred_at,
                batch_deadline=deadline,
            )
        else:
            batch = existing
        return collect_curation_item(
            self.repository,
            item.actor,
            batch,
            BatchItemWrite(
                job_id=item.job_id,
                receipt=EventReceipt(
                    event_id=item.event_id,
                    event_revision=item.event_revision,
                    status=OperationStatus.PENDING,
                    reason="batch_assignment",
                ),
                isolation_key=stored_partition_key,
            ),
        )

    def claim(self, actor: ActorContext, now: datetime) -> CurationBatch | None:
        return ready_curation_batch(self.repository, actor, now)

    def execute_claimed(
        self,
        run: ClaimedBatchRun,
        processor: CurationBatchProcessor,
    ) -> CurationBatch:
        expected = {(item.event_id, item.event_revision) for item in run.batch.event_receipts}
        supplied = {(item.request.event_id, item.request.event_revision) for item in run.items}
        if not supplied <= expected:
            conflict("curation_batch_work_unknown", run.batch.batch_id)
        results = processor.process(run)
        state = (
            BatchState.CANCELLED
            if run.cancellation is not None and run.cancellation.cancelled()
            else BatchState.COMPLETED
        )
        return finish_curation_batch(
            self.repository,
            run.actor,
            run.batch.batch_id,
            results,
            state,
        )


__all__ = [
    "BatchCurationCoordinator",
    "ClaimedBatchRun",
    "CurationBatchItem",
    "CurationBatchPolicy",
    "CurationBatchProcessor",
    "CurationBatchWork",
    "curation_partition_key",
]
