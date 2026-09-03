-- Causal marketing experiments must commit the complete Threads exposure schedule
-- before the first publication decision. Actual publication remains owned by the
-- existing human-review and Threads publisher path.
CREATE TABLE hosted_marketing_experiment_exposure_plans (
    experiment_id TEXT PRIMARY KEY REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id)
        ON DELETE RESTRICT,
    profile_id TEXT NOT NULL,
    threads_user_id_snapshot TEXT NOT NULL,
    username_snapshot TEXT NOT NULL,
    timezone_snapshot TEXT NOT NULL,
    morning_time_snapshot TEXT NOT NULL,
    evening_time_snapshot TEXT NOT NULL,
    account_revision INTEGER NOT NULL CHECK (account_revision >= 1),
    plan_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL UNIQUE CHECK (length(plan_sha256) = 64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (account_id, profile_id)
        REFERENCES hosted_threads_profiles(account_id, profile_id) ON DELETE RESTRICT
);

CREATE TRIGGER hosted_marketing_experiment_exposure_plans_immutable
BEFORE UPDATE ON hosted_marketing_experiment_exposure_plans
BEGIN
    SELECT RAISE(ABORT, 'marketing experiment exposure plan is immutable');
END;

CREATE TRIGGER hosted_marketing_causal_candidate_profile_on_assignment
BEFORE UPDATE OF marketing_assignment_id ON hosted_workspace_candidates
WHEN NEW.marketing_assignment_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM hosted_marketing_post_assignments AS assignment
      JOIN hosted_marketing_experiments AS experiment
        ON experiment.experiment_id = assignment.experiment_id
      WHERE assignment.assignment_id = NEW.marketing_assignment_id
        AND experiment.allocation_method = 'server_randomized_complete_blocks_v1'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM hosted_marketing_post_assignments AS assignment
      JOIN hosted_marketing_experiment_exposure_plans AS plan
        ON plan.experiment_id = assignment.experiment_id
      WHERE assignment.assignment_id = NEW.marketing_assignment_id
        AND plan.profile_id = NEW.threads_profile_id
  )
BEGIN
    SELECT RAISE(ABORT, 'causal candidate profile must match frozen exposure plan');
END;

CREATE TRIGGER hosted_marketing_causal_candidate_profile_immutable
BEFORE UPDATE OF threads_profile_id ON hosted_workspace_candidates
WHEN NEW.marketing_assignment_id IS NOT NULL
  AND NEW.threads_profile_id IS NOT OLD.threads_profile_id
  AND EXISTS (
      SELECT 1
      FROM hosted_marketing_post_assignments AS assignment
      JOIN hosted_marketing_experiments AS experiment
        ON experiment.experiment_id = assignment.experiment_id
      WHERE assignment.assignment_id = NEW.marketing_assignment_id
        AND experiment.allocation_method = 'server_randomized_complete_blocks_v1'
  )
BEGIN
    SELECT RAISE(ABORT, 'causal candidate profile is immutable');
END;

CREATE TABLE hosted_marketing_exposure_slots (
    slot_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES hosted_marketing_experiments(experiment_id)
        ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES hosted_marketing_post_assignments(assignment_id)
        ON DELETE RESTRICT,
    eligible_block_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL REFERENCES hosted_marketing_hypotheses(hypothesis_id)
        ON DELETE RESTRICT,
    allocation_rank INTEGER NOT NULL CHECK (allocation_rank IN (1, 2)),
    posting_slot TEXT NOT NULL CHECK (posting_slot IN ('morning', 'evening')),
    exposure_plan_sha256 TEXT NOT NULL CHECK (length(exposure_plan_sha256) = 64),
    profile_id_snapshot TEXT NOT NULL,
    threads_user_id_snapshot TEXT NOT NULL,
    username_snapshot TEXT NOT NULL,
    timezone_snapshot TEXT NOT NULL,
    wall_clock_snapshot TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    tolerance_seconds INTEGER NOT NULL CHECK (tolerance_seconds BETWEEN 60 AND 3600),
    commitment_json TEXT NOT NULL,
    commitment_sha256 TEXT NOT NULL UNIQUE CHECK (length(commitment_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (experiment_id, eligible_block_id, allocation_rank),
    UNIQUE (experiment_id, eligible_block_id, hypothesis_id)
);

CREATE INDEX hosted_marketing_exposure_slots_schedule
ON hosted_marketing_exposure_slots (experiment_id, scheduled_at);

CREATE TRIGGER hosted_marketing_exposure_slots_immutable
BEFORE UPDATE ON hosted_marketing_exposure_slots
BEGIN
    SELECT RAISE(ABORT, 'marketing exposure slots are immutable');
END;
