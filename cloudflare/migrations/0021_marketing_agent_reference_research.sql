CREATE TABLE hosted_marketing_reference_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL UNIQUE
        REFERENCES hosted_marketing_campaigns(campaign_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.reference-research.v1'),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL UNIQUE CHECK (length(snapshot_sha256) = 64),
    source_count INTEGER NOT NULL CHECK (source_count BETWEEN 2 AND 16),
    created_at TEXT NOT NULL
);

CREATE TRIGGER hosted_marketing_reference_snapshots_immutable
BEFORE UPDATE ON hosted_marketing_reference_snapshots
BEGIN
    SELECT RAISE(ABORT, 'marketing reference snapshots are immutable');
END;
