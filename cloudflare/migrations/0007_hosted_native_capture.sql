ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_state TEXT
    CHECK (capture_state IN ('queued', 'failed'));

ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_task_id TEXT;

ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_error TEXT;

ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_requested_at TEXT;

CREATE TABLE IF NOT EXISTS hosted_workspace_capture_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_revision INTEGER NOT NULL CHECK (candidate_revision >= 1),
    idempotency_key TEXT NOT NULL UNIQUE,
    task_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'succeeded', 'failed', 'unknown_side_effect')),
    result_json TEXT,
    callback_id TEXT UNIQUE,
    last_dispatched_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_candidate
ON hosted_workspace_capture_tasks (account_id, candidate_id, created_at DESC);

CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_dispatch
ON hosted_workspace_capture_tasks (state, last_dispatched_at, created_at);
