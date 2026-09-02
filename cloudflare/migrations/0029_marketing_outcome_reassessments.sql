CREATE TABLE hosted_marketing_outcome_reassessments (
    reassessment_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    evaluation_id TEXT NOT NULL REFERENCES hosted_marketing_experiment_evaluations(evaluation_id)
        ON DELETE RESTRICT,
    strategy_brief_id TEXT NOT NULL REFERENCES hosted_marketing_strategy_briefs(brief_id)
        ON DELETE RESTRICT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.marketing-reassessment.v1'),
    situation TEXT NOT NULL CHECK (
        situation IN ('experiment_result', 'performance_regression', 'tool_failure')
    ),
    reassessment_json TEXT NOT NULL,
    reassessment_sha256 TEXT NOT NULL UNIQUE CHECK (length(reassessment_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('proposed', 'superseded')),
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, evaluation_id)
);

CREATE INDEX hosted_marketing_outcome_reassessments_campaign
ON hosted_marketing_outcome_reassessments (campaign_id, created_at DESC);

CREATE TRIGGER hosted_marketing_outcome_reassessments_immutable
BEFORE UPDATE OF reassessment_json, reassessment_sha256, evaluation_id, strategy_brief_id
ON hosted_marketing_outcome_reassessments
BEGIN
    SELECT RAISE(ABORT, 'marketing outcome reassessments are immutable');
END;
