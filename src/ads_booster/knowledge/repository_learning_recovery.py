"""Discovery and authority validation for released sealed learning batches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_actor import load_partition_actor
from ads_booster.knowledge.batch_curation import CurationBatchItem, curation_partition_key
from ads_booster.knowledge.contracts import CurationBatch
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.learning_policy import LEARNING_POLICY_VERSION
from ads_booster.knowledge.repository_learning_recovery_write import (
    ReleasedLearningBatch,
    fail_released_learning_batch,
    reattach_released_learning_batch,
)
from ads_booster.knowledge.scope_contracts import ActorContext

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_RECOVERY_ROWS: TypeAdapter[list[tuple[str, str, str]]] = TypeAdapter(list[tuple[str, str, str]])


def recover_released_learning_batches(
    repository: SqliteKnowledgeRepository,
    workspace_id: str,
    now: datetime,
) -> int:
    """Reattach sealed learning jobs released from terminal batch attempts."""
    with repository.connection() as connection:
        rows = _RECOVERY_ROWS.validate_python(
            connection.execute(
                """SELECT DISTINCT batch.batch_json,partition.partition_key,
                    partition.actor_json
                FROM learning_admissions AS admission
                JOIN learning_rounds AS round_ ON round_.round_id=admission.sealed_round_id
                JOIN jobs AS job ON job.job_id=admission.job_id
                JOIN curation_batches AS batch ON batch.batch_id=admission.batch_id
                JOIN learning_batch_partitions AS partition
                    ON partition.batch_id=batch.batch_id
                WHERE admission.workspace_id=? AND admission.invalidated=0
                    AND round_.state IN ('ready','running')
                    AND job.state='queued' AND job.batch_id IS NULL
                    AND job.policy_version=? AND batch.state IN ('completed','cancelled')
                ORDER BY batch.batch_id""",
                (workspace_id, LEARNING_POLICY_VERSION),
            ).fetchall()
        )
    for batch_json, partition_key, actor_json in rows:
        released = ReleasedLearningBatch(
            batch=CurationBatch.model_validate_json(batch_json),
            partition_key=partition_key,
            bound_actor=ActorContext.model_validate_json(actor_json),
        )
        try:
            actor = load_partition_actor(repository, released.bound_actor, now)
            _require_recovery_partition(released, actor, now)
        except KnowledgePolicyError as error:
            fail_released_learning_batch(
                repository,
                released.batch.batch_id,
                f"learning_recovery_{error.code}",
            )
            continue
        reattach_released_learning_batch(repository, released)
    return len(rows)


def _require_recovery_partition(
    released: ReleasedLearningBatch,
    actor: ActorContext,
    now: datetime,
) -> None:
    batch = released.batch
    if not batch.event_receipts:
        raise KnowledgePolicyError(code="learning_recovery_batch_empty")
    receipt = batch.event_receipts[0]
    recovered_key = curation_partition_key(
        CurationBatchItem(
            job_id="learning-recovery-partition-check",
            event_id=receipt.event_id,
            event_revision=receipt.event_revision,
            actor=actor,
            policy_version=batch.policy_version,
            priority=batch.priority,
            occurred_at=now,
        )
    )
    if recovered_key != released.partition_key:
        raise KnowledgePolicyError(code="learning_recovery_partition_changed")


__all__ = ["recover_released_learning_batches"]
