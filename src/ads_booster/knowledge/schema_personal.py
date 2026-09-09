from typing import Final

PERSONAL_SCHEMA: Final = """
CREATE TABLE personal_access_scopes (
    scope_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('workspace','channel','member','channel_member')),
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
        OR (kind='channel_member' AND channel_id IS NOT NULL
            AND member_id IS NOT NULL AND session_id IS NULL)
    ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,member_id)
        REFERENCES members(workspace_id,member_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,member_id,session_id)
        REFERENCES sessions(workspace_id,member_id,session_id) ON DELETE RESTRICT
);
INSERT INTO personal_access_scopes SELECT * FROM access_scopes;
DROP TABLE access_scopes;
ALTER TABLE personal_access_scopes RENAME TO access_scopes;
CREATE TABLE personal_memory_documents (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('team','soul','core','daily','user')),
    brand_id TEXT,
    local_date TEXT,
    timezone TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    document_json TEXT NOT NULL,
    CHECK((kind='soul' AND brand_id IS NOT NULL AND local_date IS NULL)
        OR (kind='daily' AND brand_id IS NULL AND local_date IS NOT NULL)
        OR (kind IN ('team','core','user') AND brand_id IS NULL AND local_date IS NULL)),
    PRIMARY KEY(workspace_id,document_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
INSERT INTO personal_memory_documents SELECT * FROM memory_documents;
DROP TABLE memory_documents;
ALTER TABLE personal_memory_documents RENAME TO memory_documents;
CREATE UNIQUE INDEX memory_team_core_identity
ON memory_documents(workspace_id,scope_key,kind) WHERE kind IN ('team','core');
CREATE UNIQUE INDEX memory_soul_identity
ON memory_documents(workspace_id,scope_key,kind,brand_id) WHERE kind='soul';
CREATE UNIQUE INDEX memory_daily_identity
ON memory_documents(workspace_id,scope_key,kind,local_date) WHERE kind='daily';
CREATE UNIQUE INDEX memory_user_identity
ON memory_documents(workspace_id,scope_key,kind) WHERE kind='user';
CREATE TRIGGER personal_memory_scope_insert BEFORE INSERT ON memory_documents
WHEN (NEW.kind='user' AND NOT EXISTS (
    SELECT 1 FROM access_scopes WHERE scope_key=NEW.scope_key
    AND workspace_id=NEW.workspace_id AND kind='channel_member'
)) OR (NEW.kind!='user' AND EXISTS (
    SELECT 1 FROM access_scopes WHERE scope_key=NEW.scope_key AND kind='channel_member'
))
BEGIN SELECT RAISE(ABORT,'personal_memory_scope_mismatch'); END;
CREATE TRIGGER personal_memory_scope_update BEFORE UPDATE OF kind,scope_key,workspace_id
ON memory_documents
WHEN (NEW.kind='user' AND NOT EXISTS (
    SELECT 1 FROM access_scopes WHERE scope_key=NEW.scope_key
    AND workspace_id=NEW.workspace_id AND kind='channel_member'
)) OR (NEW.kind!='user' AND EXISTS (
    SELECT 1 FROM access_scopes WHERE scope_key=NEW.scope_key AND kind='channel_member'
))
BEGIN SELECT RAISE(ABORT,'personal_memory_scope_mismatch'); END;
CREATE TABLE personal_memory_view_outbox (
    item_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK(view_kind IN ('team','soul','core','daily','user')),
    unique_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed')),
    lease_owner TEXT,
    lease_expires_at TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
    error_code TEXT,
    UNIQUE(workspace_id,unique_key),
    FOREIGN KEY(workspace_id,document_id,revision_id)
        REFERENCES memory_revisions(workspace_id,document_id,revision_id) ON DELETE RESTRICT
);
INSERT INTO personal_memory_view_outbox SELECT * FROM memory_view_outbox;
DROP TABLE memory_view_outbox;
ALTER TABLE personal_memory_view_outbox RENAME TO memory_view_outbox;
CREATE INDEX memory_view_outbox_pending ON memory_view_outbox(state,item_id);
"""

__all__ = ["PERSONAL_SCHEMA"]
