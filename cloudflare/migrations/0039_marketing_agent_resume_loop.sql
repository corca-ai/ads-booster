-- Add an immutable task chain and a small host-owned loop projection without resetting root tasks.
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN head_step_sha256 TEXT
    CHECK (head_step_sha256 IS NULL OR length(head_step_sha256) = 64);
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN active_task_id TEXT;
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN loop_state TEXT NOT NULL DEFAULT 'running'
    CHECK (loop_state IN ('running', 'needs_input', 'stopped', 'delegated', 'failed'));
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN loop_revision INTEGER NOT NULL DEFAULT 1
    CHECK (loop_revision >= 1 AND loop_revision <= 4);
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN cumulative_cost_units INTEGER NOT NULL DEFAULT 0
    CHECK (cumulative_cost_units >= 0 AND cumulative_cost_units <= 48);
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN completed_steps INTEGER NOT NULL DEFAULT 0
    CHECK (completed_steps >= 0 AND completed_steps <= 2);

UPDATE hosted_marketing_agent_runs
SET active_task_id = CASE WHEN state = 'queued' THEN task_id ELSE NULL END,
    head_step_sha256 = (
        SELECT step.step_sha256 FROM hosted_marketing_agent_run_steps AS step
        WHERE step.run_id = hosted_marketing_agent_runs.run_id
        ORDER BY step.sequence DESC LIMIT 1
    ),
    loop_state = CASE
        WHEN state = 'queued' THEN 'running'
        WHEN state = 'campaign_created' THEN 'delegated'
        WHEN state = 'blocked' THEN 'stopped'
        ELSE 'failed'
    END,
    loop_revision = CASE WHEN state = 'queued' THEN 1 ELSE 2 END,
    cumulative_cost_units = COALESCE((
        SELECT SUM(receipt.actual_cost_units)
        FROM hosted_marketing_agent_run_receipts AS receipt
        WHERE receipt.run_id = hosted_marketing_agent_runs.run_id
    ), 0),
    completed_steps = (
        SELECT COUNT(*) FROM hosted_marketing_agent_run_steps AS step
        WHERE step.run_id = hosted_marketing_agent_runs.run_id
    );

CREATE TABLE hosted_marketing_agent_run_tasks (
    task_id TEXT PRIMARY KEY REFERENCES hosted_workspace_capture_tasks(task_id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES hosted_marketing_agent_runs(run_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence IN (1, 2)),
    phase TEXT NOT NULL CHECK (phase IN ('initial', 'resume')),
    parent_step_sha256 TEXT CHECK (
        parent_step_sha256 IS NULL OR length(parent_step_sha256) = 64
    ),
    root_request_sha256 TEXT NOT NULL CHECK (length(root_request_sha256) = 64),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    capability_snapshot_json TEXT NOT NULL CHECK (json_valid(capability_snapshot_json)),
    capability_snapshot_sha256 TEXT NOT NULL CHECK (length(capability_snapshot_sha256) = 64),
    resumable_scopes_json TEXT NOT NULL CHECK (json_valid(resumable_scopes_json)),
    resume_id TEXT,
    resume_request_json TEXT CHECK (
        resume_request_json IS NULL OR json_valid(resume_request_json)
    ),
    resume_request_sha256 TEXT CHECK (
        resume_request_sha256 IS NULL OR length(resume_request_sha256) = 64
    ),
    created_at TEXT NOT NULL,
    UNIQUE (run_id, sequence),
    UNIQUE (run_id, resume_id),
    CHECK (
        (phase = 'initial' AND sequence = 1 AND parent_step_sha256 IS NULL
            AND resume_id IS NULL AND resume_request_json IS NULL
            AND resume_request_sha256 IS NULL AND root_request_sha256 = request_sha256)
        OR (phase = 'resume' AND sequence = 2 AND parent_step_sha256 IS NOT NULL
            AND resume_id IS NOT NULL AND resume_request_json IS NOT NULL
            AND resume_request_sha256 IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_agent_run_tasks_run
ON hosted_marketing_agent_run_tasks (account_id, run_id, sequence);

-- Rows admitted before this migration used the non-resumable v4 worker contract. Preserve their
-- root task lineage for status/readback, but fail queued work explicitly instead of allowing a v4
-- task to remain leased forever after admission moves to feature_launch_run_v5.
INSERT INTO hosted_marketing_agent_run_tasks
    (task_id, run_id, account_id, sequence, phase, parent_step_sha256,
     root_request_sha256, request_json, request_sha256, capability_snapshot_json,
     capability_snapshot_sha256, resumable_scopes_json, created_at)
SELECT run.task_id, run.run_id, run.account_id, 1, 'initial', NULL,
       run.request_sha256, run.request_json, run.request_sha256,
       COALESCE(run.capability_snapshot_json, '{}'),
       COALESCE(run.capability_snapshot_sha256,
           '0000000000000000000000000000000000000000000000000000000000000000'),
       '[]', run.created_at
FROM hosted_marketing_agent_runs AS run;

UPDATE hosted_workspace_capture_tasks
SET state = 'failed', updated_at = CURRENT_TIMESTAMP
WHERE task_id IN (
    SELECT run.task_id FROM hosted_marketing_agent_runs AS run WHERE run.state = 'queued'
);

UPDATE hosted_marketing_agent_runs
SET state = 'failed', failure_code = 'feature_launch_resume_upgrade_required',
    active_task_id = NULL, loop_state = 'failed', loop_revision = 2,
    updated_at = CURRENT_TIMESTAMP
WHERE state = 'queued';

CREATE TRIGGER hosted_marketing_agent_run_task_immutable
BEFORE UPDATE ON hosted_marketing_agent_run_tasks
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run tasks are immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_task_delete_guard
BEFORE DELETE ON hosted_marketing_agent_run_tasks
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run tasks are append-only');
END;

CREATE TRIGGER hosted_marketing_agent_run_task_binding_guard
BEFORE INSERT ON hosted_marketing_agent_run_tasks
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_agent_runs AS run
    JOIN hosted_workspace_capture_tasks AS task ON task.task_id = NEW.task_id
    WHERE run.run_id = NEW.run_id AND run.account_id = NEW.account_id
      AND task.account_id = NEW.account_id
      AND task.kind = 'marketing_judgment'
      AND task.required_capability = 'feature_launch_run_v5'
      AND NEW.root_request_sha256 = run.request_sha256
      AND (
        (NEW.sequence = 1 AND NEW.phase = 'initial' AND NEW.task_id = run.task_id
          AND NEW.request_sha256 = run.request_sha256)
        OR
        (NEW.sequence = 2 AND NEW.phase = 'resume' AND run.state = 'blocked'
          AND run.loop_state = 'needs_input' AND run.active_task_id IS NULL
          AND run.completed_steps = 1 AND NEW.parent_step_sha256 = run.head_step_sha256)
      )
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run task binding is invalid');
END;

CREATE TABLE hosted_marketing_agent_run_task_receipts (
    task_id TEXT NOT NULL REFERENCES hosted_marketing_agent_run_tasks(task_id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES hosted_marketing_agent_runs(run_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1 AND sequence <= 3),
    entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
    entry_sha256 TEXT NOT NULL CHECK (length(entry_sha256) = 64),
    actual_cost_units INTEGER NOT NULL CHECK (actual_cost_units >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, sequence),
    UNIQUE (task_id, entry_sha256)
);

CREATE TRIGGER hosted_marketing_agent_run_task_receipt_immutable
BEFORE UPDATE ON hosted_marketing_agent_run_task_receipts
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run task receipts are immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_task_receipt_delete_guard
BEFORE DELETE ON hosted_marketing_agent_run_task_receipts
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run task receipts are append-only');
END;

CREATE TRIGGER hosted_marketing_agent_run_task_receipt_binding_guard
BEFORE INSERT ON hosted_marketing_agent_run_task_receipts
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_agent_run_tasks AS mapping
    JOIN hosted_marketing_agent_runs AS run ON run.run_id = mapping.run_id
    WHERE mapping.task_id = NEW.task_id AND mapping.run_id = NEW.run_id
      AND mapping.account_id = NEW.account_id AND mapping.phase = 'resume'
      AND run.account_id = NEW.account_id AND run.active_task_id = NEW.task_id
      AND run.state = 'queued'
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run task receipt binding is invalid');
END;

DROP TRIGGER hosted_marketing_agent_run_transition_guard;
CREATE TRIGGER hosted_marketing_agent_run_transition_guard
BEFORE UPDATE OF state, research_result_json, research_result_sha256, campaign_id, failure_code
ON hosted_marketing_agent_runs
WHEN NOT (
    (OLD.state = 'queued'
        AND NEW.state IN ('campaign_created', 'blocked', 'failed', 'unknown_side_effect'))
    OR (OLD.state = 'blocked' AND OLD.loop_state = 'needs_input'
        AND NEW.state = 'queued' AND NEW.research_result_json IS NULL
        AND NEW.research_result_sha256 IS NULL AND NEW.campaign_id IS NULL
        AND NEW.failure_code IS NULL AND NEW.active_task_id IS NOT NULL
        AND NEW.loop_revision = OLD.loop_revision + 1)
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run transition is final');
END;

DROP TRIGGER hosted_marketing_agent_run_intent_immutable;
CREATE TRIGGER hosted_marketing_agent_run_intent_immutable
BEFORE UPDATE OF intent_snapshot_json, intent_snapshot_sha256,
    next_intent_json, next_intent_sha256
ON hosted_marketing_agent_runs
WHEN NOT (
    OLD.state = 'queued'
    AND NEW.state IN ('campaign_created', 'blocked')
    AND NEW.intent_snapshot_json IS NOT NULL
    AND NEW.intent_snapshot_sha256 IS NOT NULL
    AND NEW.next_intent_json IS NOT NULL
    AND NEW.next_intent_sha256 IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run intent is immutable');
END;

DROP TRIGGER hosted_marketing_agent_run_step_binding_guard;
CREATE TRIGGER hosted_marketing_agent_run_step_binding_guard
BEFORE INSERT ON hosted_marketing_agent_run_steps
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_agent_runs AS run
    JOIN hosted_marketing_agent_run_tasks AS mapping ON mapping.task_id = NEW.task_id
    WHERE run.run_id = NEW.run_id AND run.account_id = NEW.account_id
      AND run.active_task_id = NEW.task_id AND run.state = 'queued'
      AND mapping.run_id = NEW.run_id AND mapping.account_id = NEW.account_id
      AND mapping.sequence = NEW.sequence
      AND NEW.parent_step_sha256 IS mapping.parent_step_sha256
      AND NEW.sequence = (SELECT COUNT(*) + 1 FROM hosted_marketing_agent_run_steps AS step
          WHERE step.run_id = NEW.run_id)
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run step binding is invalid');
END;
