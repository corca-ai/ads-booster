-- Freeze the host-derived observe-only capability manifest on each product run and retain the
-- worker's bound receipt chain as a separate append-only audit ledger.
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN capability_snapshot_json TEXT
    CHECK (capability_snapshot_json IS NULL OR json_valid(capability_snapshot_json));
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN capability_snapshot_sha256 TEXT
    CHECK (capability_snapshot_sha256 IS NULL OR length(capability_snapshot_sha256) = 64);

CREATE TRIGGER hosted_marketing_agent_run_capability_snapshot_immutable
BEFORE UPDATE OF capability_snapshot_json, capability_snapshot_sha256
ON hosted_marketing_agent_runs
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run capability snapshot is immutable');
END;

CREATE TABLE hosted_marketing_agent_run_receipts (
    run_id TEXT NOT NULL REFERENCES hosted_marketing_agent_runs(run_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES hosted_workspace_capture_tasks(task_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1 AND sequence <= 3),
    action_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (
        scope IN ('product_truth', 'customer_intelligence', 'market_evidence')
    ),
    call_sha256 TEXT NOT NULL CHECK (length(call_sha256) = 64),
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    observation_sha256 TEXT NOT NULL CHECK (length(observation_sha256) = 64),
    actual_cost_units INTEGER NOT NULL CHECK (actual_cost_units >= 0),
    entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
    entry_sha256 TEXT NOT NULL CHECK (length(entry_sha256) = 64),
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence),
    UNIQUE (run_id, action_id),
    UNIQUE (run_id, scope),
    UNIQUE (run_id, call_sha256),
    UNIQUE (run_id, request_sha256),
    UNIQUE (run_id, receipt_sha256),
    UNIQUE (run_id, observation_sha256)
);

CREATE INDEX hosted_marketing_agent_run_receipts_account
ON hosted_marketing_agent_run_receipts (account_id, run_id, sequence);

CREATE TRIGGER hosted_marketing_agent_run_receipt_binding_guard
BEFORE INSERT ON hosted_marketing_agent_run_receipts
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_marketing_agent_runs AS run
    WHERE run.run_id = NEW.run_id
      AND run.account_id = NEW.account_id
      AND run.task_id = NEW.task_id
      AND run.state = 'queued'
      AND run.capability_snapshot_json IS NOT NULL
      AND run.capability_snapshot_sha256 IS NOT NULL
      AND NEW.sequence = (
          SELECT COUNT(*) + 1 FROM hosted_marketing_agent_run_receipts AS receipt
          WHERE receipt.run_id = NEW.run_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run receipt binding is invalid');
END;

CREATE TRIGGER hosted_marketing_agent_run_receipt_immutable
BEFORE UPDATE ON hosted_marketing_agent_run_receipts
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run receipts are immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_receipt_delete_guard
BEFORE DELETE ON hosted_marketing_agent_run_receipts
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run receipts are append-only');
END;
