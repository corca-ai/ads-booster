ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_prompt_version TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_prompt_sha256 TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_model TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN feedback_rules_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE hosted_workspace_feedback_events ADD COLUMN candidate_revision INTEGER
CHECK (candidate_revision IS NULL OR candidate_revision >= 1);
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN candidate_snapshot_json TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN candidate_snapshot_sha256 TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN generation_prompt_version TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN generation_prompt_sha256 TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN generation_model TEXT;
ALTER TABLE hosted_workspace_feedback_events ADD COLUMN feedback_rules_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS hosted_workspace_feedback_rule_evidence
ON hosted_workspace_feedback_events (
    account_id,
    context_profile_id,
    decision,
    stage,
    rating,
    created_at DESC
);
