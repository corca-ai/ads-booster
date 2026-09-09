"""Atomic catalog transitions for released sealed learning batches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import CurationBatch, KnowledgeJob
from ads_booster.knowledge.identifiers import stable_id
from ads_booster.knowledge.learning_policy import LEARNING_POLICY_VERSION
from ads_booster.knowledge.operation_enums import BatchState, JobState
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_learning_state import finish_learning_rounds
from ads_booster.knowledge.repository_types import conflict
from ads_booster.knowledge.scope_contracts import ActorContext

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_BINDING_ROWS: TypeAdapter[list[tuple[str, str, str]]] = TypeAdapter(list[tuple[str, str, str]])


@dataclass(frozen=True, slots=True)
class ReleasedLearningBatch:
    batch: CurationBatch
    partition_key: str
    bound_actor: ActorContext


@dataclass(frozen=True, slots=True)
class _RecoveryFailure:
    batch_id: str
    reason: str


def reattach_released_learning_batch(
    repository: SqliteKnowledgeRepository,
    released: ReleasedLearningBatch,
) -> None:
    """Move one terminal attempt's unfinished sealed jobs to a fresh ready batch."""
    old_batch = released.batch
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        jobs = _released_jobs(connection, old_batch.batch_id)
        if not jobs:
            return
        binding_rows = _BINDING_ROWS.validate_python(
            connection.execute(
                """SELECT DISTINCT admission.sealed_round_id,admission.partition_key,
                    admission.actor_json
                FROM learning_admissions AS admission
                JOIN learning_rounds AS round_ ON round_.round_id=admission.sealed_round_id
                WHERE admission.batch_id=? AND admission.invalidated=0
                    AND admission.job_id IN (
                        SELECT job_id FROM jobs
                        WHERE state='queued' AND batch_id IS NULL AND policy_version=?
                    ) AND round_.state IN ('ready','running')""",
                (old_batch.batch_id, LEARNING_POLICY_VERSION),
            ).fetchall()
        )
        round_ids = {row[0] for row in binding_rows}
        actors = tuple(ActorContext.model_validate_json(row[2]) for row in binding_rows)
        binding_matches = (
            len(round_ids) == 1
            and all(row[1] == released.partition_key for row in binding_rows)
            and all(_same_bound_actor(actor, released.bound_actor) for actor in actors)
        )
        if not binding_matches:
            _terminalize(
                connection,
                jobs,
                _RecoveryFailure(old_batch.batch_id, "learning_recovery_binding_conflict"),
            )
            return
        round_id = next(iter(round_ids))
        pending_event_ids = {job.root_event_id for job in jobs}
        receipts = tuple(
            receipt for receipt in old_batch.event_receipts if receipt.event_id in pending_event_ids
        )
        if len(receipts) != len(jobs):
            _terminalize(
                connection,
                jobs,
                _RecoveryFailure(old_batch.batch_id, "learning_recovery_job_count_conflict"),
            )
            return
        new_batch = old_batch.model_copy(
            update={
                "batch_id": stable_id("curation-batch-recovery", old_batch.batch_id),
                "state": BatchState.READY,
                "event_receipts": receipts,
            }
        )
        _insert_batch(connection, new_batch, released)
        for job in jobs:
            receipt = next(item for item in receipts if item.event_id == job.root_event_id)
            _ = connection.execute(
                """INSERT INTO batch_items(
                    batch_id,event_id,event_revision,result_status,receipt_json
                ) VALUES (?,?,?,?,?)""",
                (
                    new_batch.batch_id,
                    receipt.event_id,
                    receipt.event_revision,
                    receipt.status.value,
                    receipt.model_dump_json(),
                ),
            )
            assigned = job.model_copy(
                update={
                    "state": JobState.WAITING_DEPENDENCY,
                    "batch_id": new_batch.batch_id,
                    "reason_code": "learning_batch_recovered",
                }
            )
            cursor = connection.execute(
                """UPDATE jobs SET state='waiting_dependency',batch_id=?,reason_code=?,job_json=?
                WHERE job_id=? AND state='queued' AND batch_id IS NULL""",
                (
                    assigned.batch_id,
                    assigned.reason_code,
                    assigned.model_dump_json(),
                    assigned.job_id,
                ),
            )
            if cursor.rowcount != 1:
                conflict("learning_recovery_job_fence_stale", job.job_id)
            _ = connection.execute(
                """UPDATE learning_admissions SET batch_id=?
                WHERE batch_id=? AND job_id=? AND sealed_round_id=? AND invalidated=0""",
                (new_batch.batch_id, old_batch.batch_id, job.job_id, round_id),
            )


def fail_released_learning_batch(
    repository: SqliteKnowledgeRepository,
    batch_id: str,
    reason: str,
) -> None:
    """Settle a released batch whose persisted authority can no longer be restored."""
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _terminalize(
            connection,
            _released_jobs(connection, batch_id),
            _RecoveryFailure(batch_id, reason),
        )


def _insert_batch(
    connection: sqlite3.Connection,
    batch: CurationBatch,
    released: ReleasedLearningBatch,
) -> None:
    _ = connection.execute(
        """INSERT INTO curation_batches(
            batch_id,workspace_id,scope_key,priority,policy_version,state,
            first_event_at,batch_deadline,batch_json
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            batch.batch_id,
            batch.workspace_id,
            scope_key(batch.scope),
            batch.priority.value,
            batch.policy_version,
            batch.state.value,
            batch.first_event_at.isoformat(),
            batch.batch_deadline.isoformat(),
            batch.model_dump_json(),
        ),
    )
    _ = connection.execute(
        """INSERT INTO learning_batch_partitions(batch_id,partition_key,actor_json)
        VALUES (?,?,?)""",
        (
            batch.batch_id,
            released.partition_key,
            released.bound_actor.model_dump_json(),
        ),
    )


def _released_jobs(connection: sqlite3.Connection, batch_id: str) -> tuple[KnowledgeJob, ...]:
    rows = _STRING_ROWS.validate_python(
        connection.execute(
            """SELECT DISTINCT job.job_json FROM learning_admissions AS admission
            JOIN learning_rounds AS round_ ON round_.round_id=admission.sealed_round_id
            JOIN jobs AS job ON job.job_id=admission.job_id
            WHERE admission.batch_id=? AND admission.invalidated=0
                AND round_.state IN ('ready','running')
                AND job.state='queued' AND job.batch_id IS NULL
                AND job.policy_version=? ORDER BY job.job_id""",
            (batch_id, LEARNING_POLICY_VERSION),
        ).fetchall()
    )
    return tuple(KnowledgeJob.model_validate_json(row[0]) for row in rows)


def _same_bound_actor(actor: ActorContext, bound_actor: ActorContext) -> bool:
    return (
        actor.model_copy(update={"authenticated_at": bound_actor.authenticated_at}) == bound_actor
    )


def _terminalize(
    connection: sqlite3.Connection,
    jobs: tuple[KnowledgeJob, ...],
    failure: _RecoveryFailure,
) -> None:
    for job in jobs:
        failed = job.model_copy(update={"state": JobState.FAILED, "reason_code": failure.reason})
        _ = connection.execute(
            """UPDATE jobs SET state='failed',reason_code=?,job_json=?
            WHERE job_id=? AND state='queued' AND batch_id IS NULL""",
            (failure.reason, failed.model_dump_json(), job.job_id),
        )
    finish_learning_rounds(connection, failure.batch_id)


__all__ = [
    "ReleasedLearningBatch",
    "fail_released_learning_batch",
    "reattach_released_learning_batch",
]
