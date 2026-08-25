CREATE TABLE IF NOT EXISTS shared_instructions (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_shared_instruction
ON shared_instructions (active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS marketing_accounts (
    account_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    country TEXT NOT NULL,
    timezone TEXT NOT NULL,
    schedule_minutes INTEGER NOT NULL CHECK (schedule_minutes >= 1),
    instruction_revision INTEGER NOT NULL REFERENCES shared_instructions(revision),
    credential_ref TEXT,
    adapter_mode TEXT NOT NULL CHECK (adapter_mode IN ('simulation', 'live')),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    next_run_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS due_marketing_accounts
ON marketing_accounts (enabled, next_run_at);

CREATE TABLE IF NOT EXISTS marketing_runs (
    run_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES marketing_accounts(account_id),
    workflow_instance_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    context_digest TEXT,
    publication_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS marketing_runs_account
ON marketing_runs (account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS marketing_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES marketing_runs(run_id),
    state TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS marketing_run_events_run
ON marketing_run_events (run_id, created_at);

CREATE TABLE IF NOT EXISTS marketing_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES marketing_runs(run_id),
    account_id TEXT NOT NULL REFERENCES marketing_accounts(account_id),
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('queued', 'succeeded', 'failed', 'unknown_side_effect')),
    result_json TEXT,
    callback_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS marketing_tasks_run
ON marketing_tasks (run_id, created_at);
