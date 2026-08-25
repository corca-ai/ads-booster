CREATE TABLE IF NOT EXISTS marketing_review_event_receipts (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES marketing_runs(run_id),
    account_id TEXT NOT NULL REFERENCES marketing_accounts(account_id),
    phase TEXT NOT NULL CHECK (phase IN ('candidates', 'publication')),
    body_json TEXT NOT NULL,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS marketing_review_event_receipts_run
ON marketing_review_event_receipts (run_id, created_at);
