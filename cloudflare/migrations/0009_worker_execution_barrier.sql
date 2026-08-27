ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN execution_started_at TEXT;

CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_safe_claim
ON hosted_workspace_capture_tasks
    (dispatch_mode, state, execution_started_at, lease_expires_at, created_at);
