CREATE TABLE hosted_marketing_feature_packets (
    packet_id TEXT PRIMARY KEY,
    feature_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.feature-evidence.v1'),
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN (
            'source_candidate', 'build_candidate', 'installed_confirmed', 'released', 'retracted'
        )
    ),
    repository TEXT NOT NULL,
    mutable_ref TEXT NOT NULL,
    resolved_commit_sha TEXT NOT NULL CHECK (length(resolved_commit_sha) = 40),
    tree_sha TEXT NOT NULL CHECK (length(tree_sha) = 40),
    packet_json TEXT NOT NULL,
    packet_sha256 TEXT NOT NULL UNIQUE CHECK (length(packet_sha256) = 64),
    publication_allowed INTEGER NOT NULL DEFAULT 0
        CHECK (publication_allowed IN (0, 1)),
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (feature_id, resolved_commit_sha, packet_sha256)
);

CREATE INDEX hosted_marketing_feature_packets_feature
ON hosted_marketing_feature_packets (feature_id, observed_at DESC);

CREATE TABLE hosted_marketing_product_truth_approvals (
    approval_id TEXT PRIMARY KEY,
    packet_id TEXT NOT NULL REFERENCES hosted_marketing_feature_packets(packet_id)
        ON DELETE RESTRICT,
    packet_sha256 TEXT NOT NULL CHECK (length(packet_sha256) = 64),
    approved_claim_ids_json TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reviewer_id TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    UNIQUE (packet_id, packet_sha256, reviewer_id)
);

CREATE TABLE hosted_marketing_campaigns (
    campaign_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id)
        ON DELETE CASCADE,
    feature_packet_id TEXT NOT NULL REFERENCES hosted_marketing_feature_packets(packet_id)
        ON DELETE RESTRICT,
    feature_packet_sha256 TEXT NOT NULL CHECK (length(feature_packet_sha256) = 64),
    runtime_epoch TEXT NOT NULL CHECK (runtime_epoch = 'agent_v1'),
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'assisted', 'live')),
    state TEXT NOT NULL CHECK (
        state IN (
            'evidence_candidate', 'strategy_requested', 'strategy_proposed',
            'experiment_registered', 'creative_planned', 'awaiting_review',
            'approved_for_publish', 'scheduled', 'publishing', 'published',
            'outcome_unknown', 'observing', 'evaluated', 'learning_candidate',
            'completed', 'stopped', 'failed'
        )
    ),
    projection_revision INTEGER NOT NULL DEFAULT 0 CHECK (projection_revision >= 0),
    business_outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX hosted_marketing_campaigns_account
ON hosted_marketing_campaigns (account_id, created_at DESC);

CREATE TABLE hosted_marketing_run_events (
    event_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    prior_revision INTEGER NOT NULL CHECK (prior_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision = prior_revision + 1),
    event_type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
    idempotency_key TEXT NOT NULL UNIQUE,
    causation_id TEXT,
    correlation_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('runtime', 'codex', 'human', 'tool', 'provider')),
    UNIQUE (campaign_id, sequence),
    UNIQUE (campaign_id, resulting_revision)
);

CREATE INDEX hosted_marketing_run_events_campaign
ON hosted_marketing_run_events (campaign_id, sequence);

CREATE TABLE hosted_marketing_context_receipts (
    receipt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.context-receipt.v1'),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
    feature_packet_sha256 TEXT NOT NULL CHECK (length(feature_packet_sha256) = 64),
    knowledge_snapshot_sha256 TEXT NOT NULL CHECK (length(knowledge_snapshot_sha256) = 64),
    capability_snapshot_sha256 TEXT NOT NULL CHECK (length(capability_snapshot_sha256) = 64),
    prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256) = 64),
    output_schema_sha256 TEXT NOT NULL CHECK (length(output_schema_sha256) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX hosted_marketing_context_receipts_campaign
ON hosted_marketing_context_receipts (campaign_id, created_at DESC);

CREATE TABLE hosted_marketing_strategy_briefs (
    brief_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    context_receipt_id TEXT NOT NULL REFERENCES hosted_marketing_context_receipts(receipt_id)
        ON DELETE RESTRICT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.strategy-brief.v1'),
    brief_json TEXT NOT NULL,
    brief_sha256 TEXT NOT NULL UNIQUE CHECK (length(brief_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, context_receipt_id)
);

CREATE TABLE hosted_marketing_experiments (
    experiment_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    strategy_brief_id TEXT NOT NULL REFERENCES hosted_marketing_strategy_briefs(brief_id)
        ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (
        state IN ('registered', 'running', 'observing', 'evaluated', 'inconclusive', 'stopped')
    ),
    primary_outcome_scope TEXT NOT NULL CHECK (
        primary_outcome_scope IN ('direct_response_attribution', 'estimated_treatment_effect')
    ),
    registration_json TEXT NOT NULL,
    registration_sha256 TEXT NOT NULL UNIQUE CHECK (length(registration_sha256) = 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE hosted_marketing_hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    strategy_brief_id TEXT NOT NULL REFERENCES hosted_marketing_strategy_briefs(brief_id)
        ON DELETE CASCADE,
    portfolio_role TEXT NOT NULL CHECK (portfolio_role IN ('control', 'challenger')),
    hypothesis_json TEXT NOT NULL,
    hypothesis_sha256 TEXT NOT NULL CHECK (length(hypothesis_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (strategy_brief_id, hypothesis_id)
);

CREATE TABLE hosted_marketing_experiment_arms (
    arm_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    hypothesis_id TEXT NOT NULL REFERENCES hosted_marketing_hypotheses(hypothesis_id)
        ON DELETE RESTRICT,
    treatment_json TEXT NOT NULL,
    treatment_sha256 TEXT NOT NULL CHECK (length(treatment_sha256) = 64),
    allocation_weight INTEGER NOT NULL CHECK (allocation_weight >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (experiment_id, hypothesis_id)
);

CREATE TABLE hosted_marketing_approval_grants (
    grant_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (
        scope IN ('product_truth', 'strategy', 'creative', 'publication', 'learning')
    ),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_sha256 TEXT NOT NULL CHECK (length(target_sha256) = 64),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'revoked')),
    reviewer_id TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    UNIQUE (campaign_id, scope, target_kind, target_id, target_sha256, reviewer_id)
);

CREATE TABLE hosted_marketing_tool_actions (
    action_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    grant_id TEXT REFERENCES hosted_marketing_approval_grants(grant_id) ON DELETE RESTRICT,
    capability_id TEXT NOT NULL,
    effect_class TEXT NOT NULL CHECK (effect_class IN ('none', 'local_artifact', 'external')),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'running', 'succeeded', 'failed', 'unknown_side_effect', 'canceled')
    ),
    action_json TEXT NOT NULL,
    action_sha256 TEXT NOT NULL CHECK (length(action_sha256) = 64),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER hosted_marketing_shadow_has_no_tool_actions
BEFORE INSERT ON hosted_marketing_tool_actions
WHEN EXISTS (
    SELECT 1 FROM hosted_marketing_campaigns
    WHERE campaign_id = NEW.campaign_id AND mode = 'shadow'
)
BEGIN
    SELECT RAISE(ABORT, 'shadow campaigns cannot create tool actions');
END;
