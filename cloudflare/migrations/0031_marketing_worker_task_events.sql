-- Add marketing-agent reasoning tasks to the existing worker execution timeline without changing
-- the capture/generation event contract or discarding already-recorded events.

ALTER TABLE mac_worker_task_events RENAME TO mac_worker_task_events_before_marketing;

CREATE TABLE mac_worker_task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    task_kind TEXT NOT NULL
        CHECK (task_kind IN ('capture', 'generate_candidates', 'marketing_judgment')),
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

INSERT INTO mac_worker_task_events
    (event_id, task_id, account_id, worker_id, worker_name, task_kind, event_type,
     failure_code, created_at)
SELECT event_id, task_id, account_id, worker_id, worker_name, task_kind, event_type,
       failure_code, created_at
FROM mac_worker_task_events_before_marketing;

DROP TABLE mac_worker_task_events_before_marketing;

CREATE INDEX mac_worker_task_events_account_recent
ON mac_worker_task_events (account_id, created_at DESC, event_id DESC);

CREATE INDEX mac_worker_task_events_retention
ON mac_worker_task_events (created_at);
