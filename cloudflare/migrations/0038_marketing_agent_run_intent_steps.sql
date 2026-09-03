-- Freeze the host-admitted next-intent decision and retain its research-bound decision step.
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN intent_snapshot_json TEXT
    CHECK (intent_snapshot_json IS NULL OR json_valid(intent_snapshot_json));
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN intent_snapshot_sha256 TEXT
    CHECK (intent_snapshot_sha256 IS NULL OR length(intent_snapshot_sha256) = 64);
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN next_intent_json TEXT
    CHECK (next_intent_json IS NULL OR json_valid(next_intent_json));
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN next_intent_sha256 TEXT
    CHECK (next_intent_sha256 IS NULL OR length(next_intent_sha256) = 64);

CREATE TRIGGER hosted_marketing_agent_run_intent_immutable
BEFORE UPDATE OF intent_snapshot_json, intent_snapshot_sha256,
    next_intent_json, next_intent_sha256
ON hosted_marketing_agent_runs
WHEN OLD.state != 'queued'
    OR OLD.intent_snapshot_json IS NOT NULL
    OR OLD.intent_snapshot_sha256 IS NOT NULL
    OR OLD.next_intent_json IS NOT NULL
    OR OLD.next_intent_sha256 IS NOT NULL
    OR NEW.intent_snapshot_json IS NULL
    OR NEW.intent_snapshot_sha256 IS NULL
    OR NEW.next_intent_json IS NULL
    OR NEW.next_intent_sha256 IS NULL
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run intent is immutable');
END;

CREATE TABLE hosted_marketing_agent_run_steps (
    run_id TEXT NOT NULL REFERENCES hosted_marketing_agent_runs(run_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES hosted_workspace_capture_tasks(task_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    parent_step_sha256 TEXT CHECK (
        parent_step_sha256 IS NULL OR length(parent_step_sha256) = 64
    ),
    step_type TEXT NOT NULL CHECK (step_type = 'research_intent_decision'),
    state_before_sha256 TEXT NOT NULL CHECK (length(state_before_sha256) = 64),
    research_result_sha256 TEXT NOT NULL CHECK (length(research_result_sha256) = 64),
    intent_snapshot_json TEXT NOT NULL CHECK (json_valid(intent_snapshot_json)),
    intent_snapshot_sha256 TEXT NOT NULL CHECK (length(intent_snapshot_sha256) = 64),
    decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
    decision_sha256 TEXT NOT NULL CHECK (length(decision_sha256) = 64),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    result_sha256 TEXT NOT NULL CHECK (length(result_sha256) = 64),
    disposition TEXT NOT NULL CHECK (disposition IN ('stopped', 'needs_input', 'delegated')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    step_json TEXT NOT NULL CHECK (json_valid(step_json)),
    step_sha256 TEXT NOT NULL CHECK (length(step_sha256) = 64),
    PRIMARY KEY (run_id, sequence),
    UNIQUE (run_id, research_result_sha256),
    UNIQUE (run_id, intent_snapshot_sha256),
    UNIQUE (run_id, decision_sha256),
    UNIQUE (run_id, result_sha256),
    UNIQUE (run_id, state_before_sha256),
    UNIQUE (run_id, step_sha256)
);

CREATE INDEX hosted_marketing_agent_run_steps_account
ON hosted_marketing_agent_run_steps (account_id, run_id, sequence);

CREATE TRIGGER hosted_marketing_agent_run_step_binding_guard
BEFORE INSERT ON hosted_marketing_agent_run_steps
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_marketing_agent_runs AS run
    WHERE run.run_id = NEW.run_id
      AND run.account_id = NEW.account_id
      AND run.task_id = NEW.task_id
      AND run.state = 'queued'
      AND (
          (NEW.sequence = 1 AND NEW.parent_step_sha256 IS NULL)
          OR (NEW.sequence > 1 AND NEW.parent_step_sha256 = (
              SELECT step_sha256 FROM hosted_marketing_agent_run_steps AS parent
              WHERE parent.run_id = NEW.run_id AND parent.sequence = NEW.sequence - 1
          ))
      )
      AND NEW.sequence = (
          SELECT COUNT(*) + 1 FROM hosted_marketing_agent_run_steps AS step
          WHERE step.run_id = NEW.run_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run step binding is invalid');
END;

CREATE TRIGGER hosted_marketing_agent_run_step_immutable
BEFORE UPDATE ON hosted_marketing_agent_run_steps
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run steps are immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_step_delete_guard
BEFORE DELETE ON hosted_marketing_agent_run_steps
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run steps are append-only');
END;
