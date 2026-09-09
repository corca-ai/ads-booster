from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import CurationBatch, EventReceipt, KnowledgeJob
from ads_booster.knowledge.grant_policy import (
    authorize_read,
    authorize_write,
    require_current_policy_epoch,
)
from ads_booster.knowledge.operation_enums import (
    BatchState,
    JobKind,
    JobState,
    OperationStatus,
)
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_types import conflict

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.operation_enums import JobPriority
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_BATCH_LIMIT: Final = 20
type BatchRow = tuple[str] | None
type BatchRows = list[tuple[str]]
type CandidateRow = tuple[str] | None
type EpochRow = tuple[int] | None
type ItemRow = tuple[str] | None
type JobRow = tuple[str] | None
_BATCH_ROW: TypeAdapter[BatchRow] = TypeAdapter(BatchRow)
_BATCH_ROWS: TypeAdapter[BatchRows] = TypeAdapter(BatchRows)
_CANDIDATE_ROW: TypeAdapter[CandidateRow] = TypeAdapter(CandidateRow)
_EPOCH_ROW: TypeAdapter[EpochRow] = TypeAdapter(EpochRow)
_ITEM_ROW: TypeAdapter[ItemRow] = TypeAdapter(ItemRow)
_JOB_ROW: TypeAdapter[JobRow] = TypeAdapter(JobRow)
_COUNT_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])


def curation_batch_generation(
    repository: KnowledgeRepository,
    actor: ActorContext,
    event_id: str,
) -> int:
    with repository.connection() as connection:
        _require_current_actor(connection, actor)
        row = _COUNT_ROW.validate_python(
            connection.execute(
                """SELECT COUNT(*) FROM batch_items AS item
                JOIN curation_batches AS batch USING(batch_id)
                WHERE batch.workspace_id=? AND batch.scope_key=? AND item.event_id=?
                    AND batch.state IN ('completed','cancelled')""",
                (actor.workspace_id, scope_key(actor.conversation_scope), event_id),
            ).fetchone()
        )
    return row[0]


def collect_curation_item(
    repository: KnowledgeRepository,
    actor: ActorContext,
    batch: CurationBatch,
    job_id: str,
    receipt: EventReceipt,
) -> CurationBatch:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _require_current_actor(connection, actor)
        _require_batch_binding(actor, batch, actor.authenticated_at)
        stored = _select_batch(connection, batch.batch_id)
        if stored is not None and not _same_batch_configuration(stored, batch):
            conflict("curation_batch_configuration_conflict", batch.batch_id)
        active = _CANDIDATE_ROW.validate_python(
            connection.execute(
                """
                SELECT item.batch_id FROM batch_items AS item
                JOIN curation_batches AS batch USING(batch_id)
                WHERE item.event_id=? AND item.event_revision=?
                    AND batch.state IN ('collecting','ready','running')
                LIMIT 1
                """,
                (receipt.event_id, receipt.event_revision),
            ).fetchone()
        )
        if active is not None and active[0] != batch.batch_id:
            conflict("curation_event_already_batched", receipt.event_id)
        existing = _ITEM_ROW.validate_python(
            connection.execute(
                """
                SELECT receipt_json FROM batch_items
                WHERE batch_id=? AND event_id=? AND event_revision=?
                """,
                (batch.batch_id, receipt.event_id, receipt.event_revision),
            ).fetchone()
        )
        job = _select_job(connection, job_id)
        if existing is not None:
            if stored is None or existing[0] != receipt.model_dump_json():
                conflict("curation_item_idempotency_conflict", receipt.event_id)
            _require_assigned_job(job, batch, receipt, job_id)
            return stored
        _require_pending_receipt(receipt)
        _require_collectable_job(job, batch, receipt, job_id)
        if stored is None:
            if batch.state is not BatchState.COLLECTING or batch.event_receipts:
                conflict("curation_batch_initial_state_conflict", batch.batch_id)
            current = batch.model_copy(update={"event_receipts": (receipt,)})
            _insert_batch(connection, current)
        else:
            if stored.state is not BatchState.COLLECTING:
                conflict("curation_batch_closed", batch.batch_id)
            receipts = (*stored.event_receipts, receipt)
            state = BatchState.READY if len(receipts) == _BATCH_LIMIT else stored.state
            current = stored.model_copy(update={"state": state, "event_receipts": receipts})
            _update_batch(connection, current, BatchState.COLLECTING)
        _ = connection.execute(
            """
            INSERT INTO batch_items(
                batch_id,event_id,event_revision,result_status,receipt_json
            ) VALUES (?,?,?,?,?)
            """,
            (
                batch.batch_id,
                receipt.event_id,
                receipt.event_revision,
                receipt.status.value,
                receipt.model_dump_json(),
            ),
        )
        _update_job(
            connection,
            job.model_copy(
                update={
                    "state": JobState.WAITING_DEPENDENCY,
                    "batch_id": batch.batch_id,
                    "reason_code": "batch_assignment",
                }
            ),
        )
        return current


def curation_batch(
    repository: KnowledgeRepository,
    actor: ActorContext,
    batch_id: str,
) -> CurationBatch | None:
    with repository.connection() as connection:
        _require_current_actor(connection, actor)
        batch = _select_scoped_batch(connection, actor, batch_id)
    if batch is not None:
        _require_batch_binding(actor, batch, actor.authenticated_at)
    return batch


def collecting_curation_batch(
    repository: KnowledgeRepository,
    actor: ActorContext,
    priority: JobPriority,
    policy_version: str,
    read_grant_sha256: str,
    write_capability_sha256: str,
) -> CurationBatch | None:
    with repository.connection() as connection:
        _require_current_actor(connection, actor)
        rows = _BATCH_ROWS.validate_python(
            connection.execute(
                """
                SELECT batch.batch_json FROM curation_batches AS batch
                WHERE batch.workspace_id=? AND batch.scope_key=? AND batch.priority=?
                    AND batch.policy_version=? AND batch.state='collecting'
                    AND (
                        SELECT COUNT(*) FROM batch_items AS item
                        WHERE item.batch_id=batch.batch_id
                    ) < ?
                ORDER BY batch.first_event_at,batch.batch_id
                """,
                (
                    actor.workspace_id,
                    scope_key(actor.conversation_scope),
                    priority.value,
                    policy_version,
                    _BATCH_LIMIT,
                ),
            ).fetchall()
        )
    for row in rows:
        batch = CurationBatch.model_validate_json(row[0])
        if (
            batch.read_grant_sha256 == read_grant_sha256
            and batch.write_capability_sha256 == write_capability_sha256
            and _submitter_matches(actor, batch)
        ):
            _require_batch_binding(actor, batch, actor.authenticated_at)
            return batch
    return None


def flush_curation_batches(
    repository: KnowledgeRepository,
    workspace_id: str,
    now: datetime,
) -> int:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        rows = _BATCH_ROWS.validate_python(
            connection.execute(
                """SELECT batch_json FROM curation_batches
                WHERE workspace_id=? AND state='collecting' AND priority='routine'
                    AND first_event_at<=?""",
                (workspace_id, now.isoformat()),
            ).fetchall()
        )
        for (encoded,) in rows:
            batch = CurationBatch.model_validate_json(encoded)
            _update_batch(
                connection,
                batch.model_copy(update={"state": BatchState.READY}),
                BatchState.COLLECTING,
            )
        return len(rows)


def ready_curation_batch(
    repository: KnowledgeRepository,
    actor: ActorContext,
    now: datetime,
    *,
    batch_id: str | None = None,
) -> CurationBatch | None:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _require_current_actor(connection, actor)
        row = _CANDIDATE_ROW.validate_python(
            connection.execute(
                """
                SELECT batch_id FROM curation_batches
                WHERE workspace_id=? AND scope_key=? AND (
                    state='ready' OR (state='collecting' AND batch_deadline<=?)
                )
                AND (? IS NULL OR batch_id=?)
                ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                    batch_deadline,batch_id
                LIMIT 1
                """,
                (
                    actor.workspace_id,
                    scope_key(actor.conversation_scope),
                    now.isoformat(),
                    batch_id,
                    batch_id,
                ),
            ).fetchone()
        )
        if row is None:
            return None
        batch = _select_batch(connection, row[0])
        if batch is None:
            conflict("curation_batch_not_found", row[0])
        _require_batch_binding(actor, batch, now)
        running = batch.model_copy(update={"state": BatchState.RUNNING})
        _update_batch(connection, running, batch.state)
        jobs = _jobs_for_batch(connection, batch.batch_id)
        if len(jobs) != len(batch.event_receipts):
            conflict("curation_batch_job_count_conflict", batch.batch_id)
        for job in jobs:
            if job.state is not JobState.WAITING_DEPENDENCY:
                conflict("curation_batch_job_state_conflict", job.job_id)
            _update_job(
                connection,
                job.model_copy(update={"state": JobState.RUNNING, "reason_code": None}),
            )
        return running


def finish_curation_batch(
    repository: KnowledgeRepository,
    actor: ActorContext,
    batch_id: str,
    event_receipts: tuple[EventReceipt, ...],
    state: BatchState,
) -> CurationBatch:
    _require_terminal_batch_state(state, batch_id)
    results = {(item.event_id, item.event_revision): item for item in event_receipts}
    if len(results) != len(event_receipts):
        conflict("curation_batch_result_duplicate", batch_id)
    for receipt in event_receipts:
        _require_terminal_receipt(receipt)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _require_current_actor(connection, actor)
        batch = _select_scoped_batch(connection, actor, batch_id)
        if batch is None:
            conflict("curation_batch_not_found", batch_id)
        _require_batch_binding(actor, batch, actor.authenticated_at)
        if batch.state is not BatchState.RUNNING:
            conflict("curation_batch_run_fence_stale", batch_id)
        known = {(item.event_id, item.event_revision) for item in batch.event_receipts}
        if not results.keys() <= known:
            conflict("curation_batch_result_unknown", batch_id)
        merged = tuple(
            results.get((item.event_id, item.event_revision), item) for item in batch.event_receipts
        )
        finished = batch.model_copy(update={"state": state, "event_receipts": merged})
        _update_batch(connection, finished, BatchState.RUNNING)
        for receipt in merged:
            result = results.get((receipt.event_id, receipt.event_revision))
            if result is None:
                _release_job(connection, batch_id, receipt.event_id)
            else:
                _finish_job(connection, batch_id, result)
                _ = connection.execute(
                    """
                    UPDATE batch_items SET result_status=?,receipt_json=?
                    WHERE batch_id=? AND event_id=? AND event_revision=?
                    """,
                    (
                        result.status.value,
                        result.model_dump_json(),
                        batch_id,
                        result.event_id,
                        result.event_revision,
                    ),
                )
        return finished


def _select_batch(
    connection: sqlite3.Connection,
    batch_id: str,
) -> CurationBatch | None:
    row = _BATCH_ROW.validate_python(
        connection.execute(
            "SELECT batch_json FROM curation_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
    )
    return None if row is None else CurationBatch.model_validate_json(row[0])


def _select_scoped_batch(
    connection: sqlite3.Connection,
    actor: ActorContext,
    batch_id: str,
) -> CurationBatch | None:
    row = _BATCH_ROW.validate_python(
        connection.execute(
            """
            SELECT batch_json FROM curation_batches
            WHERE batch_id=? AND workspace_id=? AND scope_key=?
            """,
            (batch_id, actor.workspace_id, scope_key(actor.conversation_scope)),
        ).fetchone()
    )
    return None if row is None else CurationBatch.model_validate_json(row[0])


def _select_job(connection: sqlite3.Connection, job_id: str) -> KnowledgeJob:
    row = _JOB_ROW.validate_python(
        connection.execute("SELECT job_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    )
    if row is None:
        conflict("curation_job_not_found", job_id)
    return KnowledgeJob.model_validate_json(row[0])


def _jobs_for_batch(
    connection: sqlite3.Connection,
    batch_id: str,
) -> tuple[KnowledgeJob, ...]:
    rows = _BATCH_ROWS.validate_python(
        connection.execute(
            "SELECT job_json FROM jobs WHERE batch_id=? ORDER BY job_id", (batch_id,)
        ).fetchall()
    )
    return tuple(KnowledgeJob.model_validate_json(row[0]) for row in rows)


def _require_current_actor(connection: sqlite3.Connection, actor: ActorContext) -> None:
    row = _EPOCH_ROW.validate_python(
        connection.execute(
            "SELECT policy_epoch FROM workspaces WHERE workspace_id=? AND state='active'",
            (actor.workspace_id,),
        ).fetchone()
    )
    if row is None:
        conflict("workspace_not_found", actor.workspace_id)
    require_current_policy_epoch(actor=actor, current_epoch=row[0])


def _require_batch_binding(
    actor: ActorContext,
    batch: CurationBatch,
    at: datetime,
) -> None:
    if (
        batch.workspace_id != actor.workspace_id
        or batch.scope != actor.conversation_scope
        or not _submitter_matches(actor, batch)
    ):
        conflict("curation_batch_scope_conflict", batch.batch_id)
    read_grant = authorize_read(actor=actor, target_scope=batch.scope, at=at)
    write_grant = authorize_write(actor=actor, target_scope=batch.scope, at=at)
    if batch.read_grant_sha256 != contract_sha256(read_grant):
        conflict("curation_batch_read_grant_conflict", batch.batch_id)
    if batch.write_capability_sha256 != contract_sha256(write_grant):
        conflict("curation_batch_write_capability_conflict", batch.batch_id)


def _submitter_matches(actor: ActorContext, batch: CurationBatch) -> bool:
    submitter = batch.submitter_actor
    return submitter is None or (
        actor.actor_id == submitter.actor_id
        and actor.member_id == submitter.member_id
        and actor.session_id == submitter.session_id
        and actor.policy_epoch == submitter.policy_epoch
    )


def _same_batch_configuration(left: CurationBatch, right: CurationBatch) -> bool:
    return (
        left.model_copy(update={"state": right.state, "event_receipts": right.event_receipts})
        == right
    )


def _require_collectable_job(
    job: KnowledgeJob,
    batch: CurationBatch,
    receipt: EventReceipt,
    job_id: str,
) -> None:
    if (
        job.kind is not JobKind.CURATION
        or job.state is not JobState.QUEUED
        or job.batch_id is not None
        or job.workspace_id != batch.workspace_id
        or job.scope != batch.scope
        or job.priority is not batch.priority
        or job.policy_version != batch.policy_version
        or job.root_event_id != receipt.event_id
    ):
        conflict("curation_job_binding_conflict", job_id)


def _require_assigned_job(
    job: KnowledgeJob,
    batch: CurationBatch,
    receipt: EventReceipt,
    job_id: str,
) -> None:
    if (
        job.kind is not JobKind.CURATION
        or job.batch_id != batch.batch_id
        or job.workspace_id != batch.workspace_id
        or job.scope != batch.scope
        or job.priority is not batch.priority
        or job.policy_version != batch.policy_version
        or job.root_event_id != receipt.event_id
    ):
        conflict("curation_job_binding_conflict", job_id)


def _require_pending_receipt(receipt: EventReceipt) -> None:
    match receipt.status:
        case OperationStatus.PENDING:
            return
        case (
            OperationStatus.APPLIED
            | OperationStatus.REPLAYED
            | OperationStatus.CONFLICT
            | OperationStatus.REJECTED
            | OperationStatus.FAILED
        ):
            conflict("curation_item_status_invalid", receipt.event_id)


def _require_terminal_receipt(receipt: EventReceipt) -> None:
    match receipt.status:
        case (
            OperationStatus.PENDING
            | OperationStatus.APPLIED
            | OperationStatus.REPLAYED
            | OperationStatus.CONFLICT
            | OperationStatus.REJECTED
            | OperationStatus.FAILED
        ):
            return


def _require_terminal_batch_state(state: BatchState, batch_id: str) -> None:
    match state:
        case BatchState.COMPLETED | BatchState.CANCELLED:
            return
        case BatchState.COLLECTING | BatchState.READY | BatchState.RUNNING:
            conflict("curation_batch_terminal_state_invalid", batch_id)


def _insert_batch(connection: sqlite3.Connection, batch: CurationBatch) -> None:
    _ = connection.execute(
        """
        INSERT INTO curation_batches(
            batch_id,workspace_id,scope_key,priority,policy_version,state,
            first_event_at,batch_deadline,batch_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
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


def _update_batch(
    connection: sqlite3.Connection,
    batch: CurationBatch,
    expected_state: BatchState,
) -> None:
    cursor = connection.execute(
        """
        UPDATE curation_batches SET state=?,batch_json=?
        WHERE batch_id=? AND state=?
        """,
        (batch.state.value, batch.model_dump_json(), batch.batch_id, expected_state.value),
    )
    if cursor.rowcount != 1:
        conflict("curation_batch_state_conflict", batch.batch_id)


def _update_job(connection: sqlite3.Connection, job: KnowledgeJob) -> None:
    cursor = connection.execute(
        """
        UPDATE jobs SET state=?,batch_id=?,reason_code=?,job_json=? WHERE job_id=?
        """,
        (
            job.state.value,
            job.batch_id,
            job.reason_code,
            job.model_dump_json(),
            job.job_id,
        ),
    )
    if cursor.rowcount != 1:
        conflict("curation_job_update_conflict", job.job_id)


def _release_job(
    connection: sqlite3.Connection,
    batch_id: str,
    event_id: str,
) -> None:
    job = _job_for_batch_event(connection, batch_id, event_id)
    _update_job(
        connection,
        job.model_copy(update={"state": JobState.QUEUED, "batch_id": None, "reason_code": None}),
    )


def _finish_job(
    connection: sqlite3.Connection,
    batch_id: str,
    receipt: EventReceipt,
) -> None:
    job = _job_for_batch_event(connection, batch_id, receipt.event_id)
    reason_code: str | None = None
    match receipt.status:
        case OperationStatus.PENDING:
            state = JobState.AWAITING_ANSWER
        case OperationStatus.FAILED:
            state = JobState.FAILED
            reason_code = receipt.reason[:160] if receipt.reason else "curation_failed"
        case (
            OperationStatus.APPLIED
            | OperationStatus.REPLAYED
            | OperationStatus.CONFLICT
            | OperationStatus.REJECTED
        ):
            state = JobState.COMPLETED
    finished = job.model_copy(update={"state": state, "reason_code": reason_code})
    cursor = connection.execute(
        """
        UPDATE jobs SET state=?,reason_code=?,result_sha256=?,job_json=?
        WHERE job_id=? AND batch_id=? AND state='running'
        """,
        (
            state.value,
            reason_code,
            contract_sha256(receipt),
            finished.model_dump_json(),
            job.job_id,
            batch_id,
        ),
    )
    if cursor.rowcount != 1:
        conflict("curation_job_run_fence_stale", job.job_id)


def _job_for_batch_event(
    connection: sqlite3.Connection,
    batch_id: str,
    event_id: str,
) -> KnowledgeJob:
    row = _JOB_ROW.validate_python(
        connection.execute(
            "SELECT job_json FROM jobs WHERE batch_id=? AND root_event_id=?",
            (batch_id, event_id),
        ).fetchone()
    )
    if row is None:
        conflict("curation_batch_job_missing", event_id)
    job = KnowledgeJob.model_validate_json(row[0])
    if job.state is not JobState.RUNNING:
        conflict("curation_job_run_fence_stale", job.job_id)
    return job


__all__ = [
    "collect_curation_item",
    "collecting_curation_batch",
    "curation_batch",
    "curation_batch_generation",
    "finish_curation_batch",
    "flush_curation_batches",
    "ready_curation_batch",
]
