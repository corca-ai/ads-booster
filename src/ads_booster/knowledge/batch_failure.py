from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import KnowledgeJob
from ads_booster.knowledge.operation_enums import BatchState, JobState, OperationStatus
from ads_booster.knowledge.repository_types import conflict

if TYPE_CHECKING:
    from ads_booster.knowledge.contracts import CurationBatch
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


def fail_unbatched_job(
    repository: SqliteKnowledgeRepository,
    job: KnowledgeJob,
    reason: str,
) -> None:
    failed = job.model_copy(update={"state": JobState.FAILED, "reason_code": reason})
    with repository.connection() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET state='failed',reason_code=?,job_json=?
            WHERE job_id=? AND workspace_id=? AND state='queued' AND batch_id IS NULL""",
            (reason, failed.model_dump_json(), job.job_id, job.workspace_id),
        )
        if cursor.rowcount != 1:
            conflict("curation_job_failure_fence_stale", job.job_id)


def fail_unclaimed_batch(
    repository: SqliteKnowledgeRepository,
    batch: CurationBatch,
    reason: str,
) -> None:
    """Settle denied unclaimed work without changing any previously committed receipt."""
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        rows = _ROWS.validate_python(
            connection.execute(
                "SELECT job_json FROM jobs WHERE batch_id=? AND state='waiting_dependency'",
                (batch.batch_id,),
            ).fetchall()
        )
        jobs = tuple(KnowledgeJob.model_validate_json(encoded) for (encoded,) in rows)
        unfinished = {job.root_event_id for job in jobs}
        receipts = tuple(
            receipt.model_copy(update={"status": OperationStatus.FAILED, "reason": reason})
            if receipt.event_id in unfinished
            else receipt
            for receipt in batch.event_receipts
        )
        cancelled = batch.model_copy(
            update={"state": BatchState.CANCELLED, "event_receipts": receipts}
        )
        cursor = connection.execute(
            """UPDATE curation_batches SET state='cancelled',batch_json=?
            WHERE batch_id=? AND workspace_id=? AND state IN ('collecting','ready')""",
            (cancelled.model_dump_json(), batch.batch_id, batch.workspace_id),
        )
        if cursor.rowcount != 1:
            conflict("curation_batch_failure_fence_stale", batch.batch_id)
        for job in jobs:
            failed = job.model_copy(update={"state": JobState.FAILED, "reason_code": reason})
            _ = connection.execute(
                "UPDATE jobs SET state='failed',reason_code=?,job_json=? WHERE job_id=?",
                (reason, failed.model_dump_json(), job.job_id),
            )
        for receipt in receipts:
            if receipt.event_id in unfinished:
                _ = connection.execute(
                    """UPDATE batch_items SET result_status=?,receipt_json=?
                    WHERE batch_id=? AND event_id=? AND event_revision=?""",
                    (
                        receipt.status.value,
                        receipt.model_dump_json(),
                        batch.batch_id,
                        receipt.event_id,
                        receipt.event_revision,
                    ),
                )
