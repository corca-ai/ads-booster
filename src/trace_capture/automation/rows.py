from __future__ import annotations

from datetime import UTC, datetime

from trace_capture.automation.models import QueueId, QueueRecord, QueueState
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.workspace import WorkspaceId

type QueueRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    float,
    int,
    int,
    str | None,
    float | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    int,
    float,
    float,
]


def queue_record(row: QueueRow) -> QueueRecord:
    return QueueRecord(
        queue_id=QueueId(row[0]),
        workspace_id=WorkspaceId(row[1]),
        idempotency_key=row[2],
        payload_digest=row[3],
        bundle=MarketingContextBundle.model_validate_json(row[4]),
        state=QueueState(row[5]),
        due_at=datetime.fromtimestamp(row[6], UTC),
        attempts=row[7],
        max_attempts=row[8],
        worker_id=row[9],
        lease_until=None if row[10] is None else datetime.fromtimestamp(row[10], UTC),
        run_id=row[11],
        run_idempotency_key=row[12],
        artifact_path=row[13],
        artifact_sha256=row[14],
        failure_code=row[15],
        revision=row[16],
        created_at=datetime.fromtimestamp(row[17], UTC),
        updated_at=datetime.fromtimestamp(row[18], UTC),
    )


def parse_row(raw: tuple[bytes | float | int | str | None, ...] | None) -> QueueRow | None:
    match raw:
        case None:
            return None
        case (
            str() as queue_id,
            str() as workspace_id,
            str() as idempotency_key,
            str() as payload_digest,
            str() as bundle_json,
            str() as state,
            float() as due_at,
            int() as attempts,
            int() as max_attempts,
            (None | str() as worker_id),
            (None | float() as lease_until),
            (None | str() as run_id),
            (None | str() as run_idempotency_key),
            (None | str() as artifact_path),
            (None | str() as artifact_sha256),
            (None | str() as failure_code),
            int() as revision,
            float() as created_at,
            float() as updated_at,
        ):
            return (
                queue_id,
                workspace_id,
                idempotency_key,
                payload_digest,
                bundle_json,
                state,
                due_at,
                attempts,
                max_attempts,
                worker_id,
                lease_until,
                run_id,
                run_idempotency_key,
                artifact_path,
                artifact_sha256,
                failure_code,
                revision,
                created_at,
                updated_at,
            )
        case _:
            msg = "automation queue database row is corrupt"
            raise RuntimeError(msg)
