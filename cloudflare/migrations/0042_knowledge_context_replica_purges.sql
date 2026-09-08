CREATE TABLE knowledge_context_replica_purges (
    transfer_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worker_id TEXT,
    cloudflare_replica_id TEXT NOT NULL UNIQUE,
    mac_replica_id TEXT NOT NULL UNIQUE,
    requested_at TEXT NOT NULL,
    mac_purged_at TEXT,
    cloudflare_purged_at TEXT,
    PRIMARY KEY (transfer_id, task_id),
    FOREIGN KEY (worker_id) REFERENCES mac_workers(worker_id) ON DELETE RESTRICT
);

CREATE INDEX knowledge_context_replica_purges_worker
ON knowledge_context_replica_purges (worker_id, mac_purged_at, requested_at);

ALTER TABLE hosted_marketing_agent_runs ADD COLUMN trusted_knowledge_json TEXT;
ALTER TABLE hosted_marketing_agent_runs ADD COLUMN knowledge_context_sha256 TEXT;
ALTER TABLE hosted_marketing_agent_run_tasks ADD COLUMN trusted_knowledge_json TEXT;
ALTER TABLE hosted_marketing_agent_run_tasks ADD COLUMN knowledge_context_sha256 TEXT;
