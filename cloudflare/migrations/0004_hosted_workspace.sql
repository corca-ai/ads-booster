CREATE TABLE IF NOT EXISTS hosted_workspace_candidates (
    candidate_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
    country TEXT NOT NULL,
    topic TEXT NOT NULL,
    caption TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    refs_json TEXT NOT NULL,
    principles_json TEXT NOT NULL,
    appium_prompt TEXT NOT NULL,
    image_inputs_json TEXT NOT NULL,
    ai_verdict TEXT,
    image_key TEXT,
    image_sha256 TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('awaiting_review', 'caption_approved', 'rejected', 'image_awaiting_review', 'submitted')
    ),
    review_note TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS hosted_workspace_candidates_account
ON hosted_workspace_candidates (account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hosted_workspace_generation_locks (
    account_id TEXT PRIMARY KEY,
    last_started_at INTEGER NOT NULL
);
