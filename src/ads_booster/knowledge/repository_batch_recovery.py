from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import CurationBatch, KnowledgeJob
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.operation_enums import BatchState, JobState

if TYPE_CHECKING:
    from ads_booster.knowledge.maintenance import KnowledgeOwner
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository

_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


def recover_running_batches(
    repository: SqliteKnowledgeRepository,
    workspace_id: str,
    owner: KnowledgeOwner,
) -> int:
    """Requeue abandoned jobs after acquiring ownership, retaining every committed receipt."""
    if owner.root.resolve() != repository.root.resolve():
        raise KnowledgePolicyError(code="knowledge_batch_owner_root_mismatch")
    owner.heartbeat()
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        rows = _ROWS.validate_python(
            connection.execute(
                "SELECT batch_json FROM curation_batches WHERE workspace_id=? AND state='running'",
                (workspace_id,),
            ).fetchall()
        )
        for (encoded,) in rows:
            batch = CurationBatch.model_validate_json(encoded)
            jobs = _ROWS.validate_python(
                connection.execute(
                    "SELECT job_json FROM jobs WHERE batch_id=? AND state='running'",
                    (batch.batch_id,),
                ).fetchall()
            )
            for (job_json,) in jobs:
                job = KnowledgeJob.model_validate_json(job_json)
                released = job.model_copy(
                    update={
                        "state": JobState.QUEUED,
                        "batch_id": None,
                        "reason_code": "batch_owner_recovered",
                    }
                )
                _ = connection.execute(
                    """UPDATE jobs SET state='queued',batch_id=NULL,
                    reason_code=?,job_json=? WHERE job_id=? AND state='running'""",
                    (released.reason_code, released.model_dump_json(), released.job_id),
                )
            cancelled = batch.model_copy(update={"state": BatchState.CANCELLED})
            _ = connection.execute(
                """UPDATE curation_batches SET state='cancelled',batch_json=?
                WHERE batch_id=? AND state='running'""",
                (cancelled.model_dump_json(), batch.batch_id),
            )
        return len(rows)
