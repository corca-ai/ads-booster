-- Channel-independent intake for one hosted marketing-agent run. The worker performs only
-- observe-only research; an exact callback may hand the result to the existing shadow campaign.
CREATE TABLE hosted_marketing_agent_runs (
    run_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.feature-launch-run-request.v1'),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    idempotency_key TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL UNIQUE REFERENCES hosted_workspace_capture_tasks(task_id)
        ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'campaign_created', 'blocked', 'failed', 'unknown_side_effect')
    ),
    research_result_json TEXT CHECK (
        research_result_json IS NULL OR json_valid(research_result_json)
    ),
    research_result_sha256 TEXT CHECK (
        research_result_sha256 IS NULL OR length(research_result_sha256) = 64
    ),
    campaign_id TEXT UNIQUE REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE RESTRICT,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, request_sha256),
    CHECK (
        (state = 'queued' AND research_result_json IS NULL
            AND research_result_sha256 IS NULL AND campaign_id IS NULL AND failure_code IS NULL)
        OR (state = 'campaign_created' AND research_result_json IS NOT NULL
            AND research_result_sha256 IS NOT NULL AND campaign_id IS NOT NULL
            AND failure_code IS NULL)
        OR (state = 'blocked' AND research_result_json IS NOT NULL
            AND research_result_sha256 IS NOT NULL AND campaign_id IS NULL
            AND failure_code IS NOT NULL)
        OR (state IN ('failed', 'unknown_side_effect') AND research_result_json IS NULL
            AND research_result_sha256 IS NULL AND campaign_id IS NULL
            AND failure_code IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_agent_runs_account
ON hosted_marketing_agent_runs (account_id, created_at DESC);

CREATE TRIGGER hosted_marketing_agent_run_identity_immutable
BEFORE UPDATE OF run_id, account_id, schema_version, request_json, request_sha256,
    idempotency_key, task_id, created_at
ON hosted_marketing_agent_runs
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run identity is immutable');
END;

CREATE TRIGGER hosted_marketing_agent_run_transition_guard
BEFORE UPDATE OF state, research_result_json, research_result_sha256, campaign_id, failure_code
ON hosted_marketing_agent_runs
WHEN NOT (
    OLD.state = 'queued'
    AND NEW.state IN ('campaign_created', 'blocked', 'failed', 'unknown_side_effect')
)
BEGIN
    SELECT RAISE(ABORT, 'marketing agent run transition is final');
END;
