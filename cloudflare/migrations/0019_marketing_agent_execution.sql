CREATE TABLE hosted_marketing_media_plans (
    plan_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    strategy_brief_id TEXT NOT NULL REFERENCES hosted_marketing_strategy_briefs(brief_id)
        ON DELETE RESTRICT,
    context_receipt_id TEXT NOT NULL REFERENCES hosted_marketing_context_receipts(receipt_id)
        ON DELETE RESTRICT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.media-plan.v1'),
    plan_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL UNIQUE CHECK (length(plan_sha256) = 64),
    publication_allowed INTEGER NOT NULL CHECK (publication_allowed IN (0, 1)),
    human_review_required INTEGER NOT NULL CHECK (human_review_required = 1),
    state TEXT NOT NULL CHECK (state IN ('proposed', 'approved', 'rejected', 'stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, strategy_brief_id, context_receipt_id)
);

CREATE TABLE hosted_marketing_creative_treatments (
    treatment_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES hosted_marketing_media_plans(plan_id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    hypothesis_id TEXT NOT NULL REFERENCES hosted_marketing_hypotheses(hypothesis_id)
        ON DELETE RESTRICT,
    format TEXT NOT NULL CHECK (
        format IN (
            'native_sequence', 'screen_recording', 'explanatory_carousel',
            'designed_static', 'text_only'
        )
    ),
    treatment_json TEXT NOT NULL,
    treatment_sha256 TEXT NOT NULL CHECK (length(treatment_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (plan_id, hypothesis_id)
);

CREATE TABLE hosted_marketing_artifact_requests (
    request_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    treatment_id TEXT NOT NULL REFERENCES hosted_marketing_creative_treatments(treatment_id)
        ON DELETE CASCADE,
    capability_id TEXT NOT NULL,
    proof_kind TEXT NOT NULL CHECK (
        proof_kind IN (
            'installed_native_capture', 'bound_screen_recording', 'composed_explanation',
            'design_render', 'copy_only'
        )
    ),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('planned', 'approved', 'executing', 'succeeded', 'failed', 'stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (treatment_id, request_id)
);

CREATE TABLE hosted_marketing_artifact_manifests (
    manifest_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    treatment_id TEXT NOT NULL REFERENCES hosted_marketing_creative_treatments(treatment_id)
        ON DELETE RESTRICT,
    request_id TEXT NOT NULL REFERENCES hosted_marketing_artifact_requests(request_id)
        ON DELETE RESTRICT,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.artifact-manifest.v1'),
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL UNIQUE CHECK (length(manifest_sha256) = 64),
    artifact_uri TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (request_id, artifact_sha256)
);

CREATE TRIGGER hosted_marketing_artifact_manifests_immutable
BEFORE UPDATE ON hosted_marketing_artifact_manifests
BEGIN
    SELECT RAISE(ABORT, 'artifact manifests are immutable');
END;

CREATE TABLE hosted_marketing_post_assignments (
    assignment_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    hypothesis_id TEXT NOT NULL REFERENCES hosted_marketing_hypotheses(hypothesis_id)
        ON DELETE RESTRICT,
    treatment_id TEXT NOT NULL REFERENCES hosted_marketing_creative_treatments(treatment_id)
        ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL REFERENCES hosted_workspace_candidates(candidate_id)
        ON DELETE RESTRICT,
    candidate_revision INTEGER NOT NULL CHECK (candidate_revision >= 1),
    candidate_content_sha256 TEXT NOT NULL CHECK (length(candidate_content_sha256) = 64),
    eligible_block_id TEXT NOT NULL,
    assignment_json TEXT NOT NULL,
    assignment_sha256 TEXT NOT NULL UNIQUE CHECK (length(assignment_sha256) = 64),
    assigned_at TEXT NOT NULL,
    UNIQUE (campaign_id, candidate_id),
    UNIQUE (experiment_id, eligible_block_id, hypothesis_id)
);

ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_campaign_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_experiment_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_hypothesis_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_treatment_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_assignment_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN marketing_assignment_sha256 TEXT;

CREATE TRIGGER hosted_marketing_candidate_assignment_insert
BEFORE INSERT ON hosted_workspace_candidates
WHEN NEW.marketing_assignment_id IS NOT NULL
BEGIN
    SELECT (CASE WHEN NOT EXISTS (
        SELECT 1 FROM hosted_marketing_post_assignments AS assignment
        JOIN hosted_marketing_campaigns AS campaign
          ON campaign.campaign_id = assignment.campaign_id
        JOIN hosted_marketing_creative_treatments AS treatment
          ON treatment.treatment_id = assignment.treatment_id
        JOIN hosted_marketing_media_plans AS plan ON plan.plan_id = treatment.plan_id
        JOIN hosted_marketing_feature_packets AS packet
          ON packet.packet_id = campaign.feature_packet_id
         AND packet.packet_sha256 = campaign.feature_packet_sha256
        JOIN hosted_marketing_product_truth_approvals AS truth
          ON truth.packet_id = packet.packet_id AND truth.packet_sha256 = packet.packet_sha256
         AND truth.decision = 'approved'
        JOIN hosted_marketing_approval_grants AS grant
          ON grant.campaign_id = campaign.campaign_id
         AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
         AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
         AND grant.decision = 'approved'
        WHERE assignment.assignment_id = NEW.marketing_assignment_id
          AND assignment.candidate_id = NEW.candidate_id
          AND assignment.campaign_id = NEW.marketing_campaign_id
          AND assignment.experiment_id = NEW.marketing_experiment_id
          AND assignment.hypothesis_id = NEW.marketing_hypothesis_id
          AND assignment.treatment_id = NEW.marketing_treatment_id
          AND assignment.assignment_sha256 = NEW.marketing_assignment_sha256
          AND campaign.account_id = NEW.account_id
          AND campaign.mode != 'shadow'
          AND packet.publication_allowed = 1
          AND plan.state = 'approved'
          AND plan.publication_allowed = 1
    ) THEN RAISE(ABORT, 'marketing candidate assignment is invalid') END);
END;

CREATE TRIGGER hosted_marketing_candidate_assignment_update
BEFORE UPDATE OF marketing_campaign_id, marketing_experiment_id, marketing_hypothesis_id,
    marketing_treatment_id, marketing_assignment_id, marketing_assignment_sha256
ON hosted_workspace_candidates
WHEN NEW.marketing_assignment_id IS NOT NULL
BEGIN
    SELECT (CASE WHEN NOT EXISTS (
        SELECT 1 FROM hosted_marketing_post_assignments AS assignment
        JOIN hosted_marketing_campaigns AS campaign
          ON campaign.campaign_id = assignment.campaign_id
        JOIN hosted_marketing_creative_treatments AS treatment
          ON treatment.treatment_id = assignment.treatment_id
        JOIN hosted_marketing_media_plans AS plan ON plan.plan_id = treatment.plan_id
        JOIN hosted_marketing_feature_packets AS packet
          ON packet.packet_id = campaign.feature_packet_id
         AND packet.packet_sha256 = campaign.feature_packet_sha256
        JOIN hosted_marketing_product_truth_approvals AS truth
          ON truth.packet_id = packet.packet_id AND truth.packet_sha256 = packet.packet_sha256
         AND truth.decision = 'approved'
        JOIN hosted_marketing_approval_grants AS grant
          ON grant.campaign_id = campaign.campaign_id
         AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
         AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
         AND grant.decision = 'approved'
        WHERE assignment.assignment_id = NEW.marketing_assignment_id
          AND assignment.candidate_id = NEW.candidate_id
          AND assignment.campaign_id = NEW.marketing_campaign_id
          AND assignment.experiment_id = NEW.marketing_experiment_id
          AND assignment.hypothesis_id = NEW.marketing_hypothesis_id
          AND assignment.treatment_id = NEW.marketing_treatment_id
          AND assignment.assignment_sha256 = NEW.marketing_assignment_sha256
          AND campaign.account_id = NEW.account_id
          AND campaign.mode != 'shadow'
          AND packet.publication_allowed = 1
          AND plan.state = 'approved'
          AND plan.publication_allowed = 1
    ) THEN RAISE(ABORT, 'marketing candidate assignment is invalid') END);
END;

CREATE TRIGGER hosted_marketing_assigned_candidate_content_immutable
BEFORE UPDATE OF caption, hypothesis, appium_prompt, image_inputs_json,
    context_snapshot_json, persona_id
ON hosted_workspace_candidates
WHEN OLD.marketing_assignment_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assigned marketing candidate content is immutable');
END;

ALTER TABLE hosted_threads_publications ADD COLUMN marketing_assignment_id TEXT;

CREATE TRIGGER hosted_marketing_publication_assignment_insert
BEFORE INSERT ON hosted_threads_publications
WHEN NEW.marketing_assignment_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_marketing_post_assignments AS assignment
         WHERE assignment.assignment_id = NEW.marketing_assignment_id
           AND assignment.candidate_id = NEW.candidate_id
           AND NOT EXISTS (
               SELECT 1 FROM hosted_marketing_artifact_requests AS request
               WHERE request.treatment_id = assignment.treatment_id
                 AND NOT EXISTS (
                     SELECT 1 FROM hosted_marketing_artifact_manifests AS manifest
                     WHERE manifest.request_id = request.request_id
                       AND manifest.treatment_id = assignment.treatment_id
                 )
           )
     )
BEGIN
    SELECT RAISE(ABORT, 'Threads publication assignment is invalid');
END;

CREATE TRIGGER hosted_marketing_publication_assignment_update
BEFORE UPDATE OF marketing_assignment_id ON hosted_threads_publications
WHEN OLD.marketing_assignment_id IS NOT NEW.marketing_assignment_id
BEGIN
    SELECT RAISE(ABORT, 'Threads publication assignment is immutable');
END;

CREATE TABLE hosted_marketing_variant_links (
    variant_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    assignment_id TEXT NOT NULL REFERENCES hosted_marketing_post_assignments(assignment_id)
        ON DELETE CASCADE,
    destination_uri TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE CHECK (length(token_sha256) = 64),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (assignment_id)
);

CREATE TABLE hosted_marketing_product_events (
    event_id TEXT PRIMARY KEY,
    event_version TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'first_open', 'feature_start', 'generation_completed',
            'scheduling_completed', 'setup_completed'
        )
    ),
    install_id_sha256 TEXT NOT NULL CHECK (length(install_id_sha256) = 64),
    variant_id TEXT NOT NULL REFERENCES hosted_marketing_variant_links(variant_id) ON DELETE RESTRICT,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    UNIQUE (install_id_sha256, event_id)
);

CREATE TABLE hosted_marketing_attribution_observations (
    observation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    assignment_id TEXT NOT NULL REFERENCES hosted_marketing_post_assignments(assignment_id)
        ON DELETE CASCADE,
    publication_id TEXT NOT NULL REFERENCES hosted_threads_publications(publication_id)
        ON DELETE CASCADE,
    product_event_id TEXT REFERENCES hosted_marketing_product_events(event_id) ON DELETE SET NULL,
    scope TEXT NOT NULL CHECK (scope = 'direct_response_attribution'),
    window_hours INTEGER NOT NULL CHECK (window_hours BETWEEN 1 AND 720),
    matched INTEGER NOT NULL CHECK (matched IN (0, 1)),
    observed_at TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    observation_sha256 TEXT NOT NULL UNIQUE CHECK (length(observation_sha256) = 64),
    CHECK (matched = (product_event_id IS NOT NULL))
);

CREATE TABLE hosted_marketing_experiment_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.experiment-evaluation.v1'),
    state TEXT NOT NULL CHECK (state IN ('evaluated', 'inconclusive', 'stopped')),
    evaluation_json TEXT NOT NULL,
    evaluation_sha256 TEXT NOT NULL UNIQUE CHECK (length(evaluation_sha256) = 64),
    evaluated_at TEXT NOT NULL,
    UNIQUE (experiment_id, evaluation_sha256)
);

CREATE TABLE hosted_marketing_learning_candidates (
    learning_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.learning-candidate.v1'),
    candidate_json TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL UNIQUE CHECK (length(candidate_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('candidate', 'approved', 'rejected', 'superseded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE hosted_marketing_principles (
    principle_id TEXT PRIMARY KEY,
    learning_id TEXT NOT NULL REFERENCES hosted_marketing_learning_candidates(learning_id)
        ON DELETE RESTRICT,
    approval_grant_id TEXT NOT NULL REFERENCES hosted_marketing_approval_grants(grant_id)
        ON DELETE RESTRICT,
    principle_json TEXT NOT NULL,
    principle_sha256 TEXT NOT NULL UNIQUE CHECK (length(principle_sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('provisional', 'durable', 'retracted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER hosted_marketing_principle_requires_exact_learning_approval
BEFORE INSERT ON hosted_marketing_principles
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_learning_candidates AS learning
    JOIN hosted_marketing_approval_grants AS grant
      ON grant.grant_id = NEW.approval_grant_id
    WHERE learning.learning_id = NEW.learning_id
      AND grant.campaign_id = learning.campaign_id
      AND grant.scope = 'learning'
      AND grant.target_kind = 'learning_candidate'
      AND grant.target_id = learning.learning_id
      AND grant.target_sha256 = learning.candidate_sha256
      AND grant.decision = 'approved'
)
BEGIN
    SELECT RAISE(ABORT, 'marketing principle requires exact learning approval');
END;
