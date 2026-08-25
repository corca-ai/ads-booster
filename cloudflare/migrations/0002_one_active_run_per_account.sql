CREATE UNIQUE INDEX IF NOT EXISTS one_active_marketing_run_per_account
ON marketing_runs (account_id)
WHERE state NOT IN ('completed', 'failed', 'rejected', 'unknown_side_effect');
