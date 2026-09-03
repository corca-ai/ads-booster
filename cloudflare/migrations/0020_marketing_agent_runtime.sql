ALTER TABLE hosted_marketing_campaigns ADD COLUMN origin_campaign_id TEXT
    REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE RESTRICT;

CREATE TRIGGER hosted_marketing_assisted_origin_insert
BEFORE INSERT ON hosted_marketing_campaigns
WHEN NEW.mode = 'assisted' AND (
    NEW.origin_campaign_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM hosted_marketing_campaigns AS origin
        WHERE origin.campaign_id = NEW.origin_campaign_id
          AND origin.account_id = NEW.account_id
          AND origin.mode = 'shadow'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'assisted marketing campaign requires a same-account shadow origin');
END;

CREATE TRIGGER hosted_marketing_campaign_origin_immutable
BEFORE UPDATE OF origin_campaign_id ON hosted_marketing_campaigns
WHEN OLD.origin_campaign_id IS NOT NEW.origin_campaign_id
BEGIN
    SELECT RAISE(ABORT, 'marketing campaign origin is immutable');
END;

CREATE TABLE hosted_marketing_materialization_reservations (
    assignment_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    hypothesis_id TEXT NOT NULL REFERENCES hosted_marketing_hypotheses(hypothesis_id)
        ON DELETE RESTRICT,
    treatment_id TEXT NOT NULL REFERENCES hosted_marketing_creative_treatments(treatment_id)
        ON DELETE RESTRICT,
    eligible_block_id TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE REFERENCES hosted_workspace_capture_tasks(task_id)
        ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('queued', 'completed', 'failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (experiment_id, eligible_block_id, hypothesis_id)
);

CREATE INDEX hosted_marketing_materialization_campaign
ON hosted_marketing_materialization_reservations (campaign_id, state, created_at);

CREATE TRIGGER hosted_marketing_variant_link_lineage_insert
BEFORE INSERT ON hosted_marketing_variant_links
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_marketing_post_assignments AS assignment
    WHERE assignment.assignment_id = NEW.assignment_id
      AND assignment.campaign_id = NEW.campaign_id
      AND assignment.experiment_id = NEW.experiment_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing variant link lineage is invalid');
END;

CREATE TRIGGER hosted_marketing_attribution_lineage_insert
BEFORE INSERT ON hosted_marketing_attribution_observations
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_post_assignments AS assignment
    JOIN hosted_threads_publications AS publication
      ON publication.marketing_assignment_id = assignment.assignment_id
    WHERE assignment.assignment_id = NEW.assignment_id
      AND assignment.campaign_id = NEW.campaign_id
      AND assignment.experiment_id = NEW.experiment_id
      AND publication.publication_id = NEW.publication_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing attribution lineage is invalid');
END;

CREATE TRIGGER hosted_marketing_attribution_event_variant_insert
BEFORE INSERT ON hosted_marketing_attribution_observations
WHEN NEW.product_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_product_events AS event
    JOIN hosted_marketing_variant_links AS variant ON variant.variant_id = event.variant_id
    WHERE event.event_id = NEW.product_event_id
      AND variant.assignment_id = NEW.assignment_id
      AND variant.campaign_id = NEW.campaign_id
      AND variant.experiment_id = NEW.experiment_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing attribution event variant is invalid');
END;
