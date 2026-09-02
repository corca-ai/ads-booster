-- Persist the outcome-informed next-experiment handoff before any compatible worker is online.
-- Requests are an outbox, drafts are inert review artifacts, and neither table grants execution.
CREATE TABLE hosted_marketing_next_experiment_requests (
    request_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    source_campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    source_feature_packet_id TEXT NOT NULL REFERENCES hosted_marketing_feature_packets(packet_id)
        ON DELETE RESTRICT,
    source_feature_packet_sha256 TEXT NOT NULL CHECK (length(source_feature_packet_sha256) = 64),
    source_strategy_brief_id TEXT NOT NULL REFERENCES hosted_marketing_strategy_briefs(brief_id)
        ON DELETE RESTRICT,
    source_strategy_sha256 TEXT NOT NULL CHECK (length(source_strategy_sha256) = 64),
    source_experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE RESTRICT,
    source_registration_sha256 TEXT NOT NULL CHECK (length(source_registration_sha256) = 64),
    source_evaluation_id TEXT NOT NULL
        REFERENCES hosted_marketing_experiment_evaluations(evaluation_id) ON DELETE RESTRICT,
    source_evaluation_sha256 TEXT NOT NULL CHECK (length(source_evaluation_sha256) = 64),
    source_reassessment_id TEXT NOT NULL
        REFERENCES hosted_marketing_outcome_reassessments(reassessment_id) ON DELETE RESTRICT,
    source_reassessment_sha256 TEXT NOT NULL CHECK (length(source_reassessment_sha256) = 64),
    knowledge_snapshot_sha256 TEXT NOT NULL CHECK (length(knowledge_snapshot_sha256) = 64),
    marketing_context_snapshot_id TEXT,
    marketing_context_snapshot_sha256 TEXT,
    agent_run_id TEXT,
    research_session_id TEXT,
    research_input_sha256 TEXT,
    research_trace_sha256 TEXT,
    research_continuation_sha256 TEXT,
    source_lineage_sha256 TEXT NOT NULL CHECK (length(source_lineage_sha256) = 64),
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.next-experiment-request.v1'),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    request_sha256 TEXT NOT NULL UNIQUE CHECK (length(request_sha256) = 64),
    idempotency_key TEXT NOT NULL UNIQUE,
    task_id TEXT UNIQUE REFERENCES hosted_workspace_capture_tasks(task_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'queued', 'completed', 'failed', 'unknown_side_effect')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, source_reassessment_id),
    UNIQUE (request_id, request_sha256),
    CHECK (
        (marketing_context_snapshot_id IS NULL) =
        (marketing_context_snapshot_sha256 IS NULL)
    ),
    CHECK (
        marketing_context_snapshot_sha256 IS NULL
        OR length(marketing_context_snapshot_sha256) = 64
    ),
    CHECK (
        (agent_run_id IS NULL) = (research_session_id IS NULL)
        AND (agent_run_id IS NULL) = (research_input_sha256 IS NULL)
        AND (agent_run_id IS NULL) = (research_trace_sha256 IS NULL)
        AND (agent_run_id IS NULL) = (research_continuation_sha256 IS NULL)
    ),
    CHECK (
        research_input_sha256 IS NULL
        OR (
            length(research_input_sha256) = 64
            AND length(research_trace_sha256) = 64
            AND length(research_continuation_sha256) = 64
        )
    ),
    CHECK (
        (state = 'pending' AND task_id IS NULL)
        OR (state != 'pending' AND task_id IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_next_experiment_requests_dispatch
ON hosted_marketing_next_experiment_requests (state, created_at);

CREATE INDEX hosted_marketing_next_experiment_requests_campaign
ON hosted_marketing_next_experiment_requests (account_id, source_campaign_id, created_at DESC);

CREATE TRIGGER hosted_marketing_next_experiment_request_source_guard
BEFORE INSERT ON hosted_marketing_next_experiment_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_campaigns AS campaign
    JOIN hosted_marketing_feature_packets AS packet
      ON packet.packet_id = campaign.feature_packet_id
     AND packet.packet_sha256 = campaign.feature_packet_sha256
    JOIN hosted_marketing_strategy_briefs AS brief
      ON brief.campaign_id = campaign.campaign_id
    JOIN hosted_marketing_experiments AS experiment
      ON experiment.campaign_id = campaign.campaign_id
     AND experiment.strategy_brief_id = brief.brief_id
    JOIN hosted_marketing_experiment_evaluations AS evaluation
      ON evaluation.campaign_id = campaign.campaign_id
     AND evaluation.experiment_id = experiment.experiment_id
    JOIN hosted_marketing_outcome_reassessments AS reassessment
      ON reassessment.campaign_id = campaign.campaign_id
     AND reassessment.evaluation_id = evaluation.evaluation_id
     AND reassessment.strategy_brief_id = brief.brief_id
    JOIN hosted_marketing_knowledge_snapshots AS knowledge
      ON knowledge.campaign_id = campaign.campaign_id
    LEFT JOIN hosted_marketing_context_snapshots AS context
      ON context.snapshot_id = campaign.marketing_context_snapshot_id
     AND context.account_id = campaign.account_id
     AND context.snapshot_sha256 = campaign.marketing_context_snapshot_sha256
    WHERE campaign.campaign_id = NEW.source_campaign_id
      AND campaign.account_id = NEW.account_id
      AND campaign.state IN ('evaluated', 'learning_candidate')
      AND packet.packet_id = NEW.source_feature_packet_id
      AND packet.packet_sha256 = NEW.source_feature_packet_sha256
      AND brief.brief_id = NEW.source_strategy_brief_id
      AND brief.brief_sha256 = NEW.source_strategy_sha256
      AND experiment.experiment_id = NEW.source_experiment_id
      AND experiment.registration_sha256 = NEW.source_registration_sha256
      AND evaluation.evaluation_id = NEW.source_evaluation_id
      AND evaluation.evaluation_sha256 = NEW.source_evaluation_sha256
      AND reassessment.reassessment_id = NEW.source_reassessment_id
      AND reassessment.reassessment_sha256 = NEW.source_reassessment_sha256
      AND knowledge.snapshot_sha256 = NEW.knowledge_snapshot_sha256
      AND campaign.marketing_context_snapshot_id IS NEW.marketing_context_snapshot_id
      AND campaign.marketing_context_snapshot_sha256 IS NEW.marketing_context_snapshot_sha256
      AND (
          NEW.marketing_context_snapshot_id IS NULL
          OR context.snapshot_id IS NOT NULL
      )
      AND campaign.agent_run_id IS NEW.agent_run_id
      AND campaign.research_session_id IS NEW.research_session_id
      AND campaign.research_input_sha256 IS NEW.research_input_sha256
      AND campaign.research_trace_sha256 IS NEW.research_trace_sha256
      AND campaign.research_continuation_sha256 IS NEW.research_continuation_sha256
)
BEGIN
    SELECT RAISE(ABORT, 'next experiment request source lineage is invalid');
END;

CREATE TRIGGER hosted_marketing_next_experiment_request_payload_immutable
BEFORE UPDATE OF account_id, source_campaign_id, source_feature_packet_id,
    source_feature_packet_sha256, source_strategy_brief_id, source_strategy_sha256,
    source_experiment_id, source_registration_sha256, source_evaluation_id,
    source_evaluation_sha256, source_reassessment_id, source_reassessment_sha256,
    knowledge_snapshot_sha256, marketing_context_snapshot_id,
    marketing_context_snapshot_sha256, agent_run_id, research_session_id,
    research_input_sha256, research_trace_sha256, research_continuation_sha256,
    source_lineage_sha256, schema_version, request_json, request_sha256, idempotency_key
ON hosted_marketing_next_experiment_requests
BEGIN
    SELECT RAISE(ABORT, 'next experiment request payload is immutable');
END;

CREATE TRIGGER hosted_marketing_next_experiment_request_transition_guard
BEFORE UPDATE OF state, task_id ON hosted_marketing_next_experiment_requests
WHEN NOT (
    (OLD.state = 'pending' AND NEW.state = 'queued'
        AND OLD.task_id IS NULL AND NEW.task_id IS NOT NULL)
    OR (OLD.state = 'queued' AND NEW.state IN ('completed', 'failed', 'unknown_side_effect')
        AND NEW.task_id IS OLD.task_id)
)
BEGIN
    SELECT RAISE(ABORT, 'next experiment request state transition is invalid');
END;

CREATE TABLE hosted_marketing_next_experiment_drafts (
    draft_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES hosted_marketing_next_experiment_requests(request_id)
        ON DELETE RESTRICT,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    source_campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    source_lineage_sha256 TEXT NOT NULL CHECK (length(source_lineage_sha256) = 64),
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.next-experiment-draft.v1'),
    draft_json TEXT NOT NULL CHECK (json_valid(draft_json)),
    draft_sha256 TEXT NOT NULL UNIQUE CHECK (length(draft_sha256) = 64),
    admission_json TEXT NOT NULL CHECK (json_valid(admission_json)),
    admission_sha256 TEXT NOT NULL UNIQUE CHECK (length(admission_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('draft', 'approved', 'rejected', 'superseded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (request_id),
    FOREIGN KEY (request_id, request_sha256)
        REFERENCES hosted_marketing_next_experiment_requests(request_id, request_sha256)
        ON DELETE RESTRICT
);

CREATE INDEX hosted_marketing_next_experiment_drafts_campaign
ON hosted_marketing_next_experiment_drafts (account_id, source_campaign_id, created_at DESC);

CREATE TRIGGER hosted_marketing_next_experiment_draft_source_guard
BEFORE INSERT ON hosted_marketing_next_experiment_drafts
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_marketing_next_experiment_requests AS request
    WHERE request.request_id = NEW.request_id
      AND request.request_sha256 = NEW.request_sha256
      AND request.account_id = NEW.account_id
      AND request.source_campaign_id = NEW.source_campaign_id
      AND request.source_lineage_sha256 = NEW.source_lineage_sha256
      AND request.state = 'queued'
      AND request.task_id IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'next experiment draft source lineage is invalid');
END;

CREATE TRIGGER hosted_marketing_next_experiment_draft_payload_immutable
BEFORE UPDATE OF request_id, request_sha256, account_id, source_campaign_id,
    source_lineage_sha256, schema_version, draft_json, draft_sha256,
    admission_json, admission_sha256, created_at
ON hosted_marketing_next_experiment_drafts
BEGIN
    SELECT RAISE(ABORT, 'next experiment draft payload is immutable');
END;

CREATE TRIGGER hosted_marketing_next_experiment_draft_review_guard
BEFORE UPDATE OF state ON hosted_marketing_next_experiment_drafts
WHEN NEW.state IN ('approved', 'rejected') AND NOT EXISTS (
    SELECT 1 FROM hosted_marketing_approval_grants AS grant
    WHERE grant.campaign_id = NEW.source_campaign_id
      AND grant.scope = 'strategy'
      AND grant.target_kind = 'next_experiment_draft'
      AND grant.target_id = NEW.draft_id
      AND grant.target_sha256 = NEW.draft_sha256
      AND grant.decision = NEW.state
)
BEGIN
    SELECT RAISE(ABORT, 'next experiment draft requires exact strategy approval');
END;

CREATE TRIGGER hosted_marketing_next_experiment_draft_transition_guard
BEFORE UPDATE OF state ON hosted_marketing_next_experiment_drafts
WHEN NOT (
    OLD.state = 'draft' AND NEW.state IN ('approved', 'rejected', 'superseded')
)
BEGIN
    SELECT RAISE(ABORT, 'next experiment draft review is final');
END;
