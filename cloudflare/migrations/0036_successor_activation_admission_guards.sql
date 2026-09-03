-- Forward-only hardening for environments that already recorded migration 0034. Recreate the
-- materialization guard so approval and source-effect state are checked at the final transition.
CREATE INDEX IF NOT EXISTS hosted_marketing_successor_activations_source
ON hosted_marketing_successor_activations (source_campaign_id, created_at DESC);

DROP TRIGGER IF EXISTS hosted_marketing_successor_campaign_admission_guard;
CREATE TRIGGER hosted_marketing_successor_campaign_admission_guard
BEFORE INSERT ON hosted_marketing_campaigns
WHEN EXISTS (
    SELECT 1 FROM hosted_marketing_successor_activations AS activation
    WHERE activation.successor_campaign_id = NEW.campaign_id
)
AND NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_successor_activations AS activation
    JOIN hosted_marketing_campaigns AS source
      ON source.campaign_id = activation.source_campaign_id
     AND source.account_id = activation.account_id
    JOIN hosted_marketing_feature_packets AS packet
      ON packet.packet_id = NEW.feature_packet_id
     AND packet.packet_sha256 = NEW.feature_packet_sha256
     AND packet.publication_allowed = 0
    JOIN hosted_marketing_approval_grants AS grant
      ON grant.grant_id = activation.approval_grant_id
     AND grant.campaign_id = activation.source_campaign_id
     AND grant.scope = 'strategy'
     AND grant.target_kind = 'next_experiment_draft'
     AND grant.target_id = activation.draft_id
     AND grant.target_sha256 = activation.draft_sha256
     AND grant.decision = 'approved'
     AND grant.reviewer_id = json_extract(activation.activation_json, '$.approved_by')
     AND grant.reviewed_at = json_extract(activation.activation_json, '$.approved_at')
    WHERE activation.successor_campaign_id = NEW.campaign_id
      AND activation.account_id = NEW.account_id
      AND activation.state = 'pending'
      AND NEW.mode = 'shadow'
      AND NEW.state = 'strategy_requested'
      AND source.state IN ('evaluated', 'learning_candidate', 'completed')
      AND NOT EXISTS (
          SELECT 1 FROM hosted_workspace_capture_tasks AS source_task
          WHERE source_task.account_id = activation.account_id
            AND source_task.run_id = activation.source_campaign_id
            AND source_task.state = 'unknown_side_effect'
      )
      AND NOT EXISTS (
          SELECT 1 FROM hosted_marketing_tool_actions AS source_action
          WHERE source_action.campaign_id = activation.source_campaign_id
            AND source_action.state = 'unknown_side_effect'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'successor campaign activation admission is no longer valid');
END;

DROP TRIGGER IF EXISTS hosted_marketing_successor_activation_materialized_guard;
CREATE TRIGGER hosted_marketing_successor_activation_materialized_guard
BEFORE UPDATE OF state, strategy_task_id ON hosted_marketing_successor_activations
WHEN NEW.state = 'activated' AND NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_campaigns AS campaign
    JOIN hosted_workspace_capture_tasks AS task
      ON task.task_id = NEW.strategy_task_id
     AND task.account_id = campaign.account_id
     AND task.run_id = campaign.campaign_id
     AND task.kind = 'marketing_judgment'
     AND task.required_capability = 'shadow_strategy_v1'
     AND json_extract(task.task_json, '$.payload.judgment') = 'shadow_strategy'
     AND json_extract(task.task_json, '$.payload.next_experiment_seed.activation_id')
         = NEW.activation_id
    WHERE campaign.campaign_id = NEW.successor_campaign_id
      AND campaign.account_id = NEW.account_id
      AND campaign.mode = 'shadow'
      AND campaign.state = 'strategy_requested'
      AND EXISTS (
          SELECT 1 FROM hosted_marketing_approval_grants AS grant
          WHERE grant.grant_id = NEW.approval_grant_id
            AND grant.campaign_id = NEW.source_campaign_id
            AND grant.scope = 'strategy'
            AND grant.target_kind = 'next_experiment_draft'
            AND grant.target_id = NEW.draft_id
            AND grant.target_sha256 = NEW.draft_sha256
            AND grant.decision = 'approved'
            AND grant.reviewer_id = json_extract(NEW.activation_json, '$.approved_by')
            AND grant.reviewed_at = json_extract(NEW.activation_json, '$.approved_at')
      )
      AND EXISTS (
          SELECT 1 FROM hosted_marketing_campaigns AS source
          WHERE source.campaign_id = NEW.source_campaign_id
            AND source.account_id = NEW.account_id
            AND source.state IN ('evaluated', 'learning_candidate', 'completed')
            AND NOT EXISTS (
                SELECT 1 FROM hosted_workspace_capture_tasks AS source_task
                WHERE source_task.account_id = NEW.account_id
                  AND source_task.run_id = NEW.source_campaign_id
                  AND source_task.state = 'unknown_side_effect'
            )
            AND NOT EXISTS (
                SELECT 1 FROM hosted_marketing_tool_actions AS source_action
                WHERE source_action.campaign_id = NEW.source_campaign_id
                  AND source_action.state = 'unknown_side_effect'
            )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'successor activation requires its exact shadow strategy task');
END;
