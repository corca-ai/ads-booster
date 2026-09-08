from typing import Final

AUTHORITY_SCHEMA: Final = """
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
    timezone TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','disabled'))
);
CREATE TABLE members (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','disabled')),
    PRIMARY KEY(workspace_id,member_id),
    UNIQUE(workspace_id,actor_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE sessions (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','closed')),
    PRIMARY KEY(workspace_id,member_id,session_id),
    FOREIGN KEY(workspace_id,member_id)
        REFERENCES members(workspace_id,member_id) ON DELETE RESTRICT
);
CREATE TABLE access_scopes (
    scope_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('workspace','member')),
    workspace_id TEXT NOT NULL,
    member_id TEXT,
    session_id TEXT,
    scope_json TEXT NOT NULL,
    CHECK(
        (kind='workspace' AND member_id IS NULL AND session_id IS NULL)
        OR (kind='member' AND member_id IS NOT NULL AND session_id IS NOT NULL)
    ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,member_id,session_id)
        REFERENCES sessions(workspace_id,member_id,session_id) ON DELETE RESTRICT
);
CREATE TABLE memberships (
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('reader','editor','admin')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    state TEXT NOT NULL CHECK(state IN ('active','disabled')),
    PRIMARY KEY(workspace_id,member_id),
    FOREIGN KEY(workspace_id,member_id)
        REFERENCES members(workspace_id,member_id) ON DELETE RESTRICT
);
CREATE TABLE brands (
    workspace_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    state TEXT NOT NULL CHECK(state IN ('active','retired')),
    brand_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,brand_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE scope_grants (
    workspace_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    capability TEXT NOT NULL CHECK(capability IN (
        'read','write','schedule','brand_voice_edit','share','purge'
    )),
    brand_id TEXT,
    policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
    effective_at TEXT NOT NULL,
    expires_at TEXT,
    grant_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,grant_id),
    CHECK((capability='brand_voice_edit' AND brand_id IS NOT NULL)
        OR (capability!='brand_voice_edit' AND brand_id IS NULL)),
    FOREIGN KEY(workspace_id,member_id)
        REFERENCES members(workspace_id,member_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE brand_events (
    workspace_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('registered','voice_adopted','retired','reactivated')),
    expected_revision INTEGER,
    event_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,event_id),
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE run_bindings (
    binding_id TEXT PRIMARY KEY,
    run_owner TEXT NOT NULL CHECK(run_owner='agent_service'),
    run_id TEXT NOT NULL,
    binding_revision INTEGER NOT NULL CHECK(binding_revision >= 1),
    workspace_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    grant_set_sha256 TEXT NOT NULL,
    policy_epoch INTEGER NOT NULL CHECK(policy_epoch >= 1),
    brand_id TEXT,
    action_kind TEXT,
    state TEXT NOT NULL CHECK(state IN ('active','closed')),
    UNIQUE(run_owner,run_id,binding_revision),
    FOREIGN KEY(workspace_id,member_id,session_id)
        REFERENCES sessions(workspace_id,member_id,session_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE task_bindings (
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    member_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action_kind TEXT NOT NULL CHECK(action_kind IN (
        'content_write','content_rewrite','content_evaluate','research','ingest','index','team_chat'
    )),
    brand_id TEXT,
    brand_catalog_revision INTEGER,
    capability_epoch INTEGER NOT NULL CHECK(capability_epoch >= 1),
    state TEXT NOT NULL CHECK(state IN ('active','closed')),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    binding_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,task_id),
    CHECK((state='active' AND closed_at IS NULL) OR (state='closed' AND closed_at IS NOT NULL)),
    FOREIGN KEY(workspace_id,member_id,session_id)
        REFERENCES sessions(workspace_id,member_id,session_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE task_overlays (
    workspace_id TEXT NOT NULL,
    overlay_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    capability_epoch INTEGER NOT NULL CHECK(capability_epoch >= 1),
    brand_id TEXT,
    overlay_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,overlay_id),
    FOREIGN KEY(workspace_id,task_id)
        REFERENCES task_bindings(workspace_id,task_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE context_receipts (
    receipt_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    task_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    brand_id TEXT,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TABLE context_dependencies (
    receipt_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    dependency_kind TEXT NOT NULL CHECK(dependency_kind IN (
        'memory','wiki_claim','source','constraint','soul_example','soul'
    )),
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    content_sha256 TEXT,
    PRIMARY KEY(receipt_id,ordinal),
    FOREIGN KEY(receipt_id) REFERENCES context_receipts(receipt_id) ON DELETE RESTRICT
);
CREATE TABLE context_transfers (
    transfer_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    brand_id TEXT,
    action_kind TEXT NOT NULL,
    run_ref TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    invocation_ref TEXT NOT NULL,
    context_sha256 TEXT NOT NULL UNIQUE,
    receipt_id TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    transfer_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','blocked','expired','purge_pending','purged')),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT,
    FOREIGN KEY(receipt_id) REFERENCES context_receipts(receipt_id) ON DELETE RESTRICT
);
CREATE TABLE transfer_dependencies (
    transfer_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    dependency_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    content_sha256 TEXT,
    PRIMARY KEY(transfer_id,ordinal),
    FOREIGN KEY(transfer_id) REFERENCES context_transfers(transfer_id) ON DELETE RESTRICT
);
CREATE INDEX transfer_dependencies_reverse
ON transfer_dependencies(dependency_kind,entity_id,revision_id);
CREATE TABLE transfer_validations (
    transfer_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('pre_dispatch','accept_result')),
    request_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    context_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted','rejected')),
    dependency_set_sha256 TEXT,
    rejection_code TEXT,
    checked_at TEXT NOT NULL,
    valid_until TEXT,
    validation_json TEXT NOT NULL,
    PRIMARY KEY(transfer_id,stage,request_id),
    FOREIGN KEY(transfer_id) REFERENCES context_transfers(transfer_id) ON DELETE RESTRICT
);
CREATE TABLE transfer_replicas (
    transfer_id TEXT NOT NULL,
    system_id TEXT NOT NULL,
    replica_id TEXT NOT NULL,
    deletion_state TEXT NOT NULL CHECK(deletion_state IN ('retained','purge_pending','purged')),
    receipt_sha256 TEXT,
    PRIMARY KEY(transfer_id,system_id,replica_id),
    FOREIGN KEY(transfer_id) REFERENCES context_transfers(transfer_id) ON DELETE RESTRICT
);
"""

__all__ = ["AUTHORITY_SCHEMA"]
