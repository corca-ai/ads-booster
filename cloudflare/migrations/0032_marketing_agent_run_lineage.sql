ALTER TABLE hosted_marketing_campaigns ADD COLUMN agent_run_id TEXT;
ALTER TABLE hosted_marketing_campaigns ADD COLUMN research_session_id TEXT;
ALTER TABLE hosted_marketing_campaigns ADD COLUMN research_input_sha256 TEXT;
ALTER TABLE hosted_marketing_campaigns ADD COLUMN research_trace_sha256 TEXT;
ALTER TABLE hosted_marketing_campaigns ADD COLUMN research_continuation_sha256 TEXT;

CREATE UNIQUE INDEX hosted_marketing_campaigns_agent_run
ON hosted_marketing_campaigns (account_id, agent_run_id)
WHERE agent_run_id IS NOT NULL;

CREATE TRIGGER hosted_marketing_campaign_lineage_insert_guard
BEFORE INSERT ON hosted_marketing_campaigns
WHEN
    (NEW.agent_run_id IS NULL) != (NEW.research_session_id IS NULL)
    OR (NEW.agent_run_id IS NULL) != (NEW.research_input_sha256 IS NULL)
    OR (NEW.agent_run_id IS NULL) != (NEW.research_trace_sha256 IS NULL)
    OR (NEW.agent_run_id IS NULL) != (NEW.research_continuation_sha256 IS NULL)
    OR (NEW.research_input_sha256 IS NOT NULL AND length(NEW.research_input_sha256) != 64)
    OR (NEW.research_trace_sha256 IS NOT NULL AND length(NEW.research_trace_sha256) != 64)
    OR (
        NEW.research_continuation_sha256 IS NOT NULL
        AND length(NEW.research_continuation_sha256) != 64
    )
BEGIN
    SELECT RAISE(ABORT, 'marketing campaign lineage must be complete and digest-bound');
END;

CREATE TRIGGER hosted_marketing_campaign_lineage_update_guard
BEFORE UPDATE OF agent_run_id, research_session_id, research_input_sha256,
    research_trace_sha256, research_continuation_sha256
ON hosted_marketing_campaigns
WHEN
    NEW.agent_run_id IS NOT OLD.agent_run_id
    OR NEW.research_session_id IS NOT OLD.research_session_id
    OR NEW.research_input_sha256 IS NOT OLD.research_input_sha256
    OR NEW.research_trace_sha256 IS NOT OLD.research_trace_sha256
    OR NEW.research_continuation_sha256 IS NOT OLD.research_continuation_sha256
BEGIN
    SELECT RAISE(ABORT, 'marketing campaign agent-run lineage is immutable');
END;
