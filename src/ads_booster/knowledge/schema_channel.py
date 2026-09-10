from typing import Final

CHANNEL_SCHEMA: Final = """
CREATE TABLE channel_access_scopes (
    scope_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('workspace','channel','member')),
    workspace_id TEXT NOT NULL,
    member_id TEXT,
    session_id TEXT,
    scope_json TEXT NOT NULL,
    channel_id TEXT,
    CHECK(
        (kind='workspace' AND member_id IS NULL AND session_id IS NULL AND channel_id IS NULL)
        OR (kind='member' AND member_id IS NOT NULL
            AND session_id IS NOT NULL AND channel_id IS NULL)
        OR (kind='channel' AND channel_id IS NOT NULL AND member_id IS NULL AND session_id IS NULL)
    ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,member_id,session_id)
        REFERENCES sessions(workspace_id,member_id,session_id) ON DELETE RESTRICT
);
INSERT INTO channel_access_scopes(scope_key,kind,workspace_id,member_id,session_id,scope_json)
SELECT scope_key,kind,workspace_id,member_id,session_id,scope_json FROM access_scopes;
DROP TABLE access_scopes;
ALTER TABLE channel_access_scopes RENAME TO access_scopes;
DROP INDEX memory_team_core_identity;
DROP INDEX memory_soul_identity;
DROP INDEX memory_daily_identity;
CREATE UNIQUE INDEX memory_team_core_identity
ON memory_documents(workspace_id,scope_key,kind) WHERE kind IN ('team','core');
CREATE UNIQUE INDEX memory_soul_identity
ON memory_documents(workspace_id,scope_key,kind,brand_id) WHERE kind='soul';
CREATE UNIQUE INDEX memory_daily_identity
ON memory_documents(workspace_id,scope_key,kind,local_date) WHERE kind='daily';
ALTER TABLE brands ADD COLUMN scope_key TEXT REFERENCES access_scopes(scope_key) ON DELETE RESTRICT;
UPDATE brands SET scope_key=(SELECT scope_key FROM access_scopes
    WHERE access_scopes.workspace_id=brands.workspace_id AND access_scopes.kind='workspace');
CREATE TRIGGER brand_scope_required_insert BEFORE INSERT ON brands
WHEN NEW.scope_key IS NULL
BEGIN SELECT RAISE(ABORT,'brand_scope_required'); END;
CREATE TRIGGER brand_scope_required_update BEFORE UPDATE OF scope_key ON brands
WHEN NEW.scope_key IS NULL
BEGIN SELECT RAISE(ABORT,'brand_scope_required'); END;
CREATE TABLE channel_grant_admissions (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    capability TEXT NOT NULL CHECK(capability IN (
        'read','write','schedule','brand_voice_edit','share','purge'
    )),
    grant_id TEXT NOT NULL,
    policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','revoked')),
    PRIMARY KEY(workspace_id,member_id,scope_key,capability),
    FOREIGN KEY(workspace_id,member_id)
        REFERENCES members(workspace_id,member_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
"""

__all__ = ["CHANNEL_SCHEMA"]
