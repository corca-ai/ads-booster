-- Freeze the approved marketing knowledge that a campaign may consume.  A later
-- learning approval is useful for the next campaign, but must not silently alter
-- an in-flight experiment's strategy, creative, or candidate treatment.
CREATE TABLE hosted_marketing_knowledge_snapshots (
    campaign_id TEXT PRIMARY KEY REFERENCES hosted_marketing_campaigns(campaign_id)
        ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.marketing-knowledge.v1'),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
    created_at TEXT NOT NULL
);

-- Existing campaigns already froze these values in their strategy task. Preserve that exact
-- payload/digest when the runtime is upgraded, rather than silently replacing it with today's
-- principles. `json(...)` keeps the array structural inside the snapshot object.
INSERT INTO hosted_marketing_knowledge_snapshots
    (campaign_id, schema_version, snapshot_json, snapshot_sha256, created_at)
SELECT campaign.campaign_id,
       'trace.marketing-knowledge.v1',
       json_object('principles', json_extract(task.task_json, '$.payload.canonical_principles')),
       json_extract(task.task_json, '$.payload.knowledge_snapshot_sha256'),
       campaign.created_at
FROM hosted_marketing_campaigns AS campaign
JOIN hosted_workspace_capture_tasks AS task
  ON task.account_id = campaign.account_id
 AND json_extract(task.task_json, '$.payload.campaign_id') = campaign.campaign_id
 AND json_extract(task.task_json, '$.payload.judgment') IN ('market_research', 'shadow_strategy')
WHERE json_type(task.task_json, '$.payload.canonical_principles') = 'array'
  AND length(json_extract(task.task_json, '$.payload.knowledge_snapshot_sha256')) = 64
ON CONFLICT(campaign_id) DO NOTHING;
