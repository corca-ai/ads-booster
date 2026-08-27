CREATE TABLE IF NOT EXISTS mac_worker_enrollments (
    enrollment_id TEXT PRIMARY KEY,
    code_sha256 TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    pool TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    worker_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mac_worker_enrollments_expiry
ON mac_worker_enrollments (used_at, expires_at);

CREATE TABLE IF NOT EXISTS mac_workers (
    worker_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    pool TEXT NOT NULL,
    token_sha256 TEXT UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'draining', 'revoked')),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    doctor_json TEXT NOT NULL DEFAULT '{}',
    version TEXT,
    last_seen_at TEXT,
    current_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mac_workers_availability
ON mac_workers (state, last_seen_at, pool);

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN dispatch_mode TEXT NOT NULL
    DEFAULT 'legacy_queue'
    CHECK (dispatch_mode IN ('legacy_queue', 'worker_broker'));

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN worker_id TEXT;

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN lease_id TEXT;

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN lease_expires_at TEXT;

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN lease_started_at TEXT;

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN lease_accepted_at TEXT;

ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_worker_claim
ON hosted_workspace_capture_tasks
    (dispatch_mode, state, lease_expires_at, created_at);

CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_worker_owner
ON hosted_workspace_capture_tasks (worker_id, state, created_at);
