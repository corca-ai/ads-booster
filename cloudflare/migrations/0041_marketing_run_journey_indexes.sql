-- RunJourney expands only children of the current bounded frontier. This index keeps assisted
-- origin lookup proportional to that frontier instead of the account's total campaign history.
CREATE INDEX hosted_marketing_campaigns_assisted_origin
ON hosted_marketing_campaigns (account_id, origin_campaign_id, created_at, campaign_id)
WHERE mode = 'assisted' AND origin_campaign_id IS NOT NULL;

-- Activated successor traversal is also parent-scoped and deterministic.
CREATE INDEX hosted_marketing_successor_activations_source_state
ON hosted_marketing_successor_activations
    (account_id, source_campaign_id, state, updated_at, successor_campaign_id);

-- Hydration is capped at 100 journey nodes. These indexes make every latest-owner lookup local to
-- one campaign instead of repeating a tenant-wide evaluation or learning scan for each node.
CREATE INDEX hosted_marketing_experiment_evaluations_campaign_latest
ON hosted_marketing_experiment_evaluations
    (campaign_id, evaluated_at DESC, evaluation_id DESC);

CREATE INDEX hosted_marketing_learning_candidates_campaign_latest
ON hosted_marketing_learning_candidates
    (campaign_id, created_at DESC, learning_id DESC);
