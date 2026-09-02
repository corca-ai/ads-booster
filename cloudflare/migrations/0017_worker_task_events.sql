CREATE TABLE IF NOT EXISTS mac_worker_task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    task_kind TEXT NOT NULL
        CHECK (task_kind IN ('capture', 'generate_candidates')),
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'preparation_started',
            'preparation_failed',
            'execution_started',
            'execution_succeeded',
            'execution_failed',
            'execution_unknown',
            'callback_applied'
        )),
    failure_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, event_type)
);

CREATE INDEX IF NOT EXISTS mac_worker_task_events_account_recent
ON mac_worker_task_events (account_id, created_at DESC, event_id DESC);

CREATE INDEX IF NOT EXISTS mac_worker_task_events_retention
ON mac_worker_task_events (created_at);
