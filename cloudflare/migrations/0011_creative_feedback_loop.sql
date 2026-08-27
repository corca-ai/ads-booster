ALTER TABLE hosted_workspace_candidates ADD COLUMN last_review_stage TEXT
CHECK (last_review_stage IS NULL OR last_review_stage IN ('caption', 'image'));

ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_provenance_json TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN candidate_revision INTEGER;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN capture_task_id TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN artifact_sha256 TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN generation_provenance_json TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN context_snapshot_json TEXT;

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN context_snapshot_sha256 TEXT;

CREATE TABLE IF NOT EXISTS hosted_workspace_feedback_rules (
    rule_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    profile_scope TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL CHECK (stage IN ('caption', 'image')),
    target TEXT NOT NULL,
    tag TEXT NOT NULL,
    instruction TEXT NOT NULL,
    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 3),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (account_id, profile_scope, stage, target, tag)
);

CREATE INDEX IF NOT EXISTS hosted_workspace_feedback_rules_scope
ON hosted_workspace_feedback_rules (account_id, profile_scope, stage, enabled, evidence_count DESC);
