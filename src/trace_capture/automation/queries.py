from __future__ import annotations

from typing import Final


def _sql(*parts: str) -> str:
    return " ".join(parts)


COLUMNS: Final = _sql(
    "queue_id, workspace_id, idempotency_key, payload_digest, bundle_json, state, due_at,",
    "attempts, max_attempts, worker_id, lease_until, run_id, run_idempotency_key,",
    "artifact_path, artifact_sha256, failure_code, revision, created_at, updated_at",
)
SELECT_IDEMPOTENCY: Final = _sql(
    "SELECT", COLUMNS, "FROM automation_queue WHERE workspace_id = ? AND idempotency_key = ?"
)
SELECT_SCOPED: Final = _sql(
    "SELECT", COLUMNS, "FROM automation_queue WHERE workspace_id = ? AND queue_id = ?"
)
SELECT_WORKSPACE: Final = _sql(
    "SELECT",
    COLUMNS,
    "FROM automation_queue WHERE workspace_id = ?",
    "ORDER BY created_at DESC, queue_id DESC",
)
SELECT_ID: Final = _sql("SELECT", COLUMNS, "FROM automation_queue WHERE queue_id = ?")
INSERT_SUBMITTED: Final = _sql(
    "INSERT INTO automation_queue VALUES",
    "(?, ?, ?, ?, ?, 'submitted', ?, 0, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1, ?, ?)",
)
EXPIRE_RUNNING: Final = _sql(
    "UPDATE automation_queue SET state = 'failed', failure_code = 'unknown_side_effect',",
    "worker_id = NULL, lease_until = NULL, revision = revision + 1, updated_at = ?",
    "WHERE state = 'running' AND lease_until <= ?",
)
EXHAUST_CLAIM: Final = _sql(
    "UPDATE automation_queue SET state = 'failed', failure_code = 'claim_lease_exhausted',",
    "worker_id = NULL, lease_until = NULL, revision = revision + 1, updated_at = ?",
    "WHERE state = 'claimed' AND lease_until < ? AND attempts >= max_attempts",
)
RECOVER_CLAIM: Final = _sql(
    "UPDATE automation_queue SET state = 'submitted', worker_id = NULL, lease_until = NULL,",
    "revision = revision + 1, updated_at = ?",
    "WHERE state = 'claimed' AND lease_until < ? AND attempts < max_attempts",
)
SELECT_ACTIVE: Final = "SELECT 1 FROM automation_queue WHERE state IN ('claimed', 'running')"
SELECT_DUE: Final = _sql(
    "SELECT queue_id FROM automation_queue",
    "WHERE state = 'submitted' AND due_at <= ?",
    "ORDER BY due_at, created_at, queue_id LIMIT 1",
)
CLAIM: Final = _sql(
    "UPDATE automation_queue SET state = 'claimed', attempts = attempts + 1, worker_id = ?,",
    "lease_until = ?, revision = revision + 1, updated_at = ?",
    "WHERE queue_id = ? AND state = 'submitted'",
)
START: Final = _sql(
    "UPDATE automation_queue SET state = 'running', revision = revision + 1, updated_at = ?",
    "WHERE queue_id = ? AND state = 'claimed' AND worker_id = ? AND revision = ?",
)
FINISH: Final = _sql(
    "UPDATE automation_queue SET state = ?, worker_id = NULL, lease_until = NULL, run_id = ?,",
    "run_idempotency_key = ?, artifact_path = ?, artifact_sha256 = ?, failure_code = ?,",
    "revision = revision + 1, updated_at = ?",
    "WHERE queue_id = ? AND state = 'running' AND worker_id = ? AND revision = ?",
)
REVIEW: Final = _sql(
    "UPDATE automation_queue SET state = ?, revision = revision + 1, updated_at = ?",
    "WHERE queue_id = ? AND workspace_id = ? AND state = 'review' AND revision = ?",
)
