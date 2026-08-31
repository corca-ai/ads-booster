-- Bind an image retry to the exact review event that requested it. The candidate stores
-- only the pointer; the private note and reviewed artifact identity stay in the event row.
ALTER TABLE hosted_workspace_candidates ADD COLUMN last_image_feedback_event_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_feedback_context_sha256 TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN capture_feedback_application_sha256 TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN capture_task_id TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN artifact_sha256 TEXT;

-- New feedback-aware work must not be leased by an older worker that can parse the task
-- kind but does not understand the feedback envelope. NULL keeps pre-migration tasks
-- leaseable by their original workers.
ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN required_capability TEXT;

-- A promoted rule can be disabled without deleting the review evidence that produced it.
-- `context_scope` is either a real profile id or the literal `unprofiled`; using a string
-- keeps the primary key unambiguous instead of relying on SQLite NULL uniqueness.
CREATE TABLE IF NOT EXISTS hosted_workspace_feedback_rule_overrides (
    account_id TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('caption', 'image')),
    rule_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, context_scope, stage, rule_id)
);

CREATE INDEX IF NOT EXISTS hosted_workspace_feedback_distinct_candidates
ON hosted_workspace_feedback_events (
    account_id, context_profile_id, stage, decision, rating, candidate_id
);
