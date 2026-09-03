-- A validated propose callback records its no-effect decision before the server-owned campaign
-- owner is invoked. Reconciliation may safely resume after either side of campaign creation.
CREATE TABLE hosted_marketing_agent_run_delegations (
    delegation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES hosted_marketing_agent_runs(run_id) ON DELETE RESTRICT,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES hosted_marketing_agent_run_tasks(task_id) ON DELETE RESTRICT,
    step_sha256 TEXT NOT NULL UNIQUE CHECK (length(step_sha256) = 64),
    research_result_sha256 TEXT NOT NULL CHECK (length(research_result_sha256) = 64),
    campaign_id TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'trace.marketing-agent-shadow-delegation.v1'
    ),
    delegation_json TEXT NOT NULL CHECK (json_valid(delegation_json)),
    delegation_sha256 TEXT NOT NULL UNIQUE CHECK (length(delegation_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('pending', 'finalized')),
    campaign_task_id TEXT REFERENCES hosted_workspace_capture_tasks(task_id) ON DELETE RESTRICT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 12),
    next_attempt_at TEXT,
    last_failure_code TEXT CHECK (
        last_failure_code IS NULL OR last_failure_code = 'delegation_reconcile_failed'
    ),
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK (
        (state = 'pending' AND campaign_task_id IS NULL AND finalized_at IS NULL)
        OR (state = 'finalized' AND campaign_task_id IS NOT NULL AND finalized_at IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_agent_run_delegations_pending
ON hosted_marketing_agent_run_delegations (state, next_attempt_at, created_at);

CREATE TRIGGER hosted_marketing_agent_run_delegation_insert_guard
BEFORE INSERT ON hosted_marketing_agent_run_delegations
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_agent_runs AS run
    JOIN hosted_marketing_agent_run_tasks AS mapping ON mapping.task_id = NEW.task_id
    JOIN hosted_marketing_agent_run_steps AS step
      ON step.run_id = NEW.run_id AND step.task_id = NEW.task_id
     AND step.sequence = mapping.sequence AND step.step_sha256 = NEW.step_sha256
    WHERE run.run_id = NEW.run_id AND run.account_id = NEW.account_id
      AND run.active_task_id = NEW.task_id AND run.state = 'queued'
      AND mapping.run_id = NEW.run_id AND mapping.account_id = NEW.account_id
      AND step.disposition = 'delegated'
      AND step.research_result_sha256 = NEW.research_result_sha256
      AND json_extract(step.decision_json, '$.intent_id') = 'propose_shadow_strategy'
      AND NEW.campaign_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegation binding is invalid');
END;

CREATE TRIGGER hosted_marketing_agent_run_delegation_payload_immutable
BEFORE UPDATE OF delegation_id, run_id, account_id, task_id, step_sha256,
    research_result_sha256, campaign_id, schema_version, delegation_json,
    delegation_sha256, created_at
ON hosted_marketing_agent_run_delegations
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegation payload is immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_delegation_delete_guard
BEFORE DELETE ON hosted_marketing_agent_run_delegations
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegations are append-only');
END;

CREATE TRIGGER hosted_marketing_agent_run_delegation_transition_guard
BEFORE UPDATE OF state, campaign_task_id, finalized_at
ON hosted_marketing_agent_run_delegations
WHEN NOT (
    OLD.state = 'pending' AND NEW.state = 'finalized'
    AND NEW.campaign_task_id IS NOT NULL AND NEW.finalized_at IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegation transition is final');
END;

CREATE TRIGGER hosted_marketing_agent_run_delegation_retry_guard
BEFORE UPDATE OF attempt_count, next_attempt_at, last_failure_code
ON hosted_marketing_agent_run_delegations
WHEN NOT (
    OLD.state = 'pending' AND NEW.state = 'pending'
    AND NEW.attempt_count = MIN(OLD.attempt_count + 1, 12)
    AND NEW.next_attempt_at IS NOT NULL
    AND NEW.last_failure_code = 'delegation_reconcile_failed'
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegation retry is invalid');
END;

CREATE TRIGGER hosted_marketing_agent_run_delegation_materialized_guard
BEFORE UPDATE OF state, campaign_task_id
ON hosted_marketing_agent_run_delegations
WHEN NEW.state = 'finalized' AND NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_campaigns AS campaign
    JOIN hosted_workspace_capture_tasks AS task
      ON task.task_id = NEW.campaign_task_id
     AND task.account_id = campaign.account_id
     AND task.run_id = 'research-' || campaign.campaign_id
     AND task.kind = 'marketing_judgment'
     AND task.required_capability = 'market_research_v1'
     AND json_extract(task.task_json, '$.payload.judgment') = 'market_research'
    WHERE campaign.campaign_id = NEW.campaign_id
      AND campaign.account_id = NEW.account_id
      AND campaign.agent_run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent delegation campaign binding is invalid');
END;

DROP TRIGGER hosted_marketing_agent_run_transition_guard;
CREATE TRIGGER hosted_marketing_agent_run_transition_guard
BEFORE UPDATE OF state, research_result_json, research_result_sha256, campaign_id, failure_code
ON hosted_marketing_agent_runs
WHEN NOT (
    (OLD.state = 'queued'
        AND NEW.state IN ('blocked', 'failed', 'unknown_side_effect')
        AND (
            NEW.failure_code IS NOT 'shadow_strategy_delegation_pending'
            OR EXISTS (
                SELECT 1 FROM hosted_marketing_agent_run_delegations AS delegation
                WHERE delegation.run_id = OLD.run_id
                  AND delegation.account_id = OLD.account_id
                  AND delegation.state = 'pending'
                  AND delegation.step_sha256 = NEW.head_step_sha256
                  AND delegation.research_result_sha256 = NEW.research_result_sha256
            )
        ))
    OR (OLD.state = 'blocked' AND OLD.loop_state = 'needs_input'
        AND NEW.state = 'queued' AND NEW.research_result_json IS NULL
        AND NEW.research_result_sha256 IS NULL AND NEW.campaign_id IS NULL
        AND NEW.failure_code IS NULL AND NEW.active_task_id IS NOT NULL
        AND NEW.loop_revision = OLD.loop_revision + 1)
    OR (OLD.state = 'blocked' AND OLD.failure_code = 'shadow_strategy_delegation_pending'
        AND OLD.loop_state = 'running' AND OLD.active_task_id IS NULL
        AND NEW.state = 'campaign_created' AND NEW.campaign_id = OLD.run_id
        AND NEW.research_result_json = OLD.research_result_json
        AND NEW.research_result_sha256 = OLD.research_result_sha256
        AND NEW.failure_code IS NULL AND NEW.loop_state = 'delegated'
        AND NEW.loop_revision = OLD.loop_revision
        AND EXISTS (
            SELECT 1 FROM hosted_marketing_agent_run_delegations AS delegation
            WHERE delegation.run_id = OLD.run_id AND delegation.account_id = OLD.account_id
              AND delegation.state = 'pending' AND delegation.campaign_id = NEW.campaign_id
        ))
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run transition is final');
END;
