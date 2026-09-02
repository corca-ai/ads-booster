-- A causal conclusion is available only for a server-owned allocation plan.
-- Existing descriptive experiments retain their balanced, non-causal default.
ALTER TABLE hosted_marketing_experiments
    ADD COLUMN allocation_method TEXT NOT NULL DEFAULT 'balanced_complete_blocks'
    CHECK (allocation_method IN (
        'balanced_complete_blocks',
        'server_randomized_complete_blocks_v1'
    ));

ALTER TABLE hosted_marketing_experiments
    ADD COLUMN randomization_seed TEXT;

ALTER TABLE hosted_marketing_experiments
    ADD COLUMN randomization_seed_sha256 TEXT
    CHECK (randomization_seed_sha256 IS NULL OR length(randomization_seed_sha256) = 64);

ALTER TABLE hosted_marketing_materialization_reservations
    ADD COLUMN allocation_rank INTEGER NOT NULL DEFAULT 0 CHECK (allocation_rank >= 0);

ALTER TABLE hosted_marketing_post_assignments
    ADD COLUMN allocation_rank INTEGER NOT NULL DEFAULT 0 CHECK (allocation_rank >= 0);

CREATE TRIGGER hosted_marketing_experiment_randomization_immutable
BEFORE UPDATE OF allocation_method, randomization_seed, randomization_seed_sha256
ON hosted_marketing_experiments
WHEN OLD.allocation_method IS NOT NEW.allocation_method
  OR OLD.randomization_seed IS NOT NEW.randomization_seed
  OR OLD.randomization_seed_sha256 IS NOT NEW.randomization_seed_sha256
BEGIN
    SELECT RAISE(ABORT, 'marketing experiment randomization plan is immutable');
END;
