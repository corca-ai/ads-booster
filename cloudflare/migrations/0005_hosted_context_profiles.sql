CREATE TABLE IF NOT EXISTS hosted_workspace_context_profiles (
    account_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    country TEXT NOT NULL,
    name TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    situation TEXT NOT NULL,
    tone TEXT NOT NULL,
    guidance TEXT NOT NULL,
    reference_ids_json TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('starter', 'custom')),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, profile_id)
);

CREATE INDEX IF NOT EXISTS hosted_workspace_context_profiles_account
ON hosted_workspace_context_profiles (account_id, enabled, country, name);

CREATE UNIQUE INDEX IF NOT EXISTS hosted_workspace_context_profiles_default
ON hosted_workspace_context_profiles (account_id) WHERE is_default = 1 AND enabled = 1;

ALTER TABLE hosted_workspace_candidates ADD COLUMN context_profile_id TEXT;
ALTER TABLE hosted_workspace_candidates ADD COLUMN context_snapshot_json TEXT;
