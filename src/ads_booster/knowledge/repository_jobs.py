from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import KnowledgeJob
from ads_booster.knowledge.operation_enums import JobState
from ads_booster.knowledge.repository_source import _insert_job
from ads_booster.knowledge.repository_types import (
    JobClaim,
    JobCompletion,
    JobLease,
    JobRegistration,
    conflict,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_STRING = TypeAdapter(str)
_INTEGER = TypeAdapter(int)


def put_job(repository: KnowledgeRepository, registration: JobRegistration) -> None:
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT unique_key,job_json FROM jobs WHERE job_id=?",
                    (registration.job.job_id,),
                ).fetchone(),
            )
            if row is not None:
                if (
                    _STRING.validate_python(row[0]) == registration.unique_key
                    and KnowledgeJob.model_validate_json(_STRING.validate_python(row[1]))
                    == registration.job
                ):
                    return
                conflict("job_idempotency_conflict", registration.job.job_id)
            _insert_job(connection, registration.job, registration.unique_key)
    except sqlite3.IntegrityError as error:
        conflict("job_unique_key_conflict", registration.job.job_id).with_traceback(
            error.__traceback__
        )


def claim_job(
    repository: KnowledgeRepository,
    claim: JobClaim,
) -> JobLease | None:
    if claim.lease_until <= claim.now:
        conflict("job_lease_window_invalid", claim.worker_id)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT job_id,job_json,lease_generation FROM jobs
                WHERE state='queued' AND due_at<=?
                ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,due_at,job_id LIMIT 1
                """,
                (claim.now.isoformat(),),
            ).fetchone(),
        )
        if row is None:
            return None
        job_id = _STRING.validate_python(row[0])
        generation = _INTEGER.validate_python(row[2]) + 1
        job = KnowledgeJob.model_validate_json(_STRING.validate_python(row[1])).model_copy(
            update={"state": JobState.RUNNING, "lease_generation": generation}
        )
        cursor = connection.execute(
            """
            UPDATE jobs SET state='running',lease_owner=?,lease_expires_at=?,
                lease_generation=?,job_json=?
            WHERE job_id=? AND state='queued'
            """,
            (
                claim.worker_id,
                claim.lease_until.isoformat(),
                generation,
                job.model_dump_json(),
                job_id,
            ),
        )
        if cursor.rowcount != 1:
            conflict("job_claim_conflict", job_id)
        return JobLease(
            job=job,
            worker_id=claim.worker_id,
            lease_generation=generation,
            lease_until=claim.lease_until,
        )


def finish_job(repository: KnowledgeRepository, completion: JobCompletion) -> None:
    if completion.state not in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
        conflict("job_completion_state_invalid", completion.job_id)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT job_json FROM jobs WHERE job_id=?",
                (completion.job_id,),
            ).fetchone(),
        )
        if row is None:
            conflict("job_not_found", completion.job_id)
        job = KnowledgeJob.model_validate_json(_STRING.validate_python(row[0])).model_copy(
            update={"state": completion.state}
        )
        cursor = connection.execute(
            """
            UPDATE jobs SET state=?,lease_owner=NULL,lease_expires_at=NULL,
                result_sha256=?,job_json=?
            WHERE job_id=? AND state='running' AND lease_owner=? AND lease_generation=?
            """,
            (
                completion.state.value,
                completion.result_sha256,
                job.model_dump_json(),
                completion.job_id,
                completion.worker_id,
                completion.lease_generation,
            ),
        )
        if cursor.rowcount != 1:
            conflict("job_lease_stale", completion.job_id)


__all__ = ["claim_job", "finish_job", "put_job"]
