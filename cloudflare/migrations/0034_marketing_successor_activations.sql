-- A human-approved next-experiment draft gains no direct execution authority. This immutable
-- activation outbox may create exactly one successor shadow campaign and its strategy task.
CREATE TABLE hosted_marketing_successor_activations (
    activation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    source_campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE RESTRICT,
    source_lineage_sha256 TEXT NOT NULL CHECK (length(source_lineage_sha256) = 64),
    request_id TEXT NOT NULL REFERENCES hosted_marketing_next_experiment_requests(request_id)
        ON DELETE RESTRICT,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    draft_id TEXT NOT NULL REFERENCES hosted_marketing_next_experiment_drafts(draft_id)
        ON DELETE RESTRICT,
    draft_sha256 TEXT NOT NULL CHECK (length(draft_sha256) = 64),
    approval_grant_id TEXT NOT NULL REFERENCES hosted_marketing_approval_grants(grant_id)
        ON DELETE RESTRICT,
    successor_campaign_id TEXT NOT NULL UNIQUE,
    strategy_task_id TEXT UNIQUE REFERENCES hosted_workspace_capture_tasks(task_id)
        ON DELETE RESTRICT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.successor-activation.v1'),
    activation_json TEXT NOT NULL CHECK (json_valid(activation_json)),
    activation_sha256 TEXT NOT NULL UNIQUE CHECK (length(activation_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('pending', 'activated', 'blocked')),
    blocker_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (draft_id, draft_sha256),
    CHECK (
        (state = 'pending' AND strategy_task_id IS NULL AND blocker_code IS NULL)
        OR (state = 'activated' AND strategy_task_id IS NOT NULL AND blocker_code IS NULL)
        OR (state = 'blocked' AND strategy_task_id IS NULL AND blocker_code IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_successor_activations_dispatch
ON hosted_marketing_successor_activations (state, created_at);

CREATE TRIGGER hosted_marketing_successor_activation_source_guard
BEFORE INSERT ON hosted_marketing_successor_activations
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_next_experiment_drafts AS draft
    JOIN hosted_marketing_next_experiment_requests AS request
      ON request.request_id = draft.request_id
     AND request.request_sha256 = draft.request_sha256
    JOIN hosted_marketing_approval_grants AS grant
      ON grant.grant_id = NEW.approval_grant_id
     AND grant.campaign_id = draft.source_campaign_id
     AND grant.scope = 'strategy'
     AND grant.target_kind = 'next_experiment_draft'
     AND grant.target_id = draft.draft_id
     AND grant.target_sha256 = draft.draft_sha256
     AND grant.decision = 'approved'
    WHERE draft.draft_id = NEW.draft_id
      AND draft.draft_sha256 = NEW.draft_sha256
      AND draft.account_id = NEW.account_id
      AND draft.source_campaign_id = NEW.source_campaign_id
      AND draft.source_lineage_sha256 = NEW.source_lineage_sha256
      AND draft.state = 'approved'
      AND request.request_id = NEW.request_id
      AND request.request_sha256 = NEW.request_sha256
      AND request.state = 'completed'
)
BEGIN
    SELECT RAISE(ABORT, 'successor activation requires one exact approved draft lineage');
END;

CREATE TRIGGER hosted_marketing_successor_activation_payload_immutable
BEFORE UPDATE OF account_id, source_campaign_id, source_lineage_sha256, request_id,
    request_sha256, draft_id, draft_sha256, approval_grant_id, successor_campaign_id,
    schema_version, activation_json, activation_sha256, created_at
ON hosted_marketing_successor_activations
BEGIN
    SELECT RAISE(ABORT, 'successor activation payload is immutable');
END;

CREATE TRIGGER hosted_marketing_successor_activation_transition_guard
BEFORE UPDATE OF state, strategy_task_id, blocker_code
ON hosted_marketing_successor_activations
WHEN NOT (
    OLD.state = 'pending'
    AND (
        (NEW.state = 'activated' AND NEW.strategy_task_id IS NOT NULL
            AND NEW.blocker_code IS NULL)
        OR (NEW.state = 'blocked' AND NEW.strategy_task_id IS NULL
            AND NEW.blocker_code IS NOT NULL)
    )
)
BEGIN
    SELECT RAISE(ABORT, 'successor activation transition is final');
END;

CREATE TRIGGER hosted_marketing_successor_activation_materialized_guard
BEFORE UPDATE OF state, strategy_task_id ON hosted_marketing_successor_activations
WHEN NEW.state = 'activated' AND NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_campaigns AS campaign
    JOIN hosted_workspace_capture_tasks AS task
      ON task.task_id = NEW.strategy_task_id
     AND task.account_id = campaign.account_id
     AND task.run_id = campaign.campaign_id
     AND task.kind = 'marketing_judgment'
     AND task.required_capability = 'shadow_strategy_v1'
     AND json_extract(task.task_json, '$.payload.judgment') = 'shadow_strategy'
     AND json_extract(task.task_json, '$.payload.next_experiment_seed.activation_id')
         = NEW.activation_id
    WHERE campaign.campaign_id = NEW.successor_campaign_id
      AND campaign.account_id = NEW.account_id
      AND campaign.mode = 'shadow'
      AND campaign.state = 'strategy_requested'
)
BEGIN
    SELECT RAISE(ABORT, 'successor activation requires its exact shadow strategy task');
END;

