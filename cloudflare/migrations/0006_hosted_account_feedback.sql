CREATE TABLE IF NOT EXISTS hosted_workspace_accounts (
    account_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    timezone TEXT NOT NULL,
    morning_time TEXT NOT NULL,
    evening_time TEXT NOT NULL,
    generation_enabled INTEGER NOT NULL DEFAULT 0 CHECK (generation_enabled IN (0, 1)),
    next_generation_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS hosted_workspace_accounts_generation
ON hosted_workspace_accounts (generation_enabled, enabled, next_generation_at);

ALTER TABLE hosted_workspace_candidates ADD COLUMN posting_slot TEXT
CHECK (posting_slot IS NULL OR posting_slot IN ('morning', 'evening', 'manual'));

ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_batch_id TEXT;

ALTER TABLE hosted_workspace_candidates ADD COLUMN last_review_rating INTEGER
CHECK (last_review_rating IS NULL OR last_review_rating BETWEEN 1 AND 5);

ALTER TABLE hosted_workspace_candidates ADD COLUMN last_review_tags_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS hosted_workspace_candidates_batch
ON hosted_workspace_candidates (account_id, generation_batch_id, posting_slot);

CREATE TABLE IF NOT EXISTS hosted_workspace_feedback_events (
    event_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    context_profile_id TEXT,
    stage TEXT NOT NULL CHECK (stage IN ('caption', 'image')),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    tags_json TEXT NOT NULL,
    note TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS hosted_workspace_feedback_account
ON hosted_workspace_feedback_events (account_id, context_profile_id, created_at DESC);
