from typing import Final


DELETION_SCHEMA: Final = """
CREATE TABLE deletion_requests (
    request_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN (
        'source','page','claim','memory_document','memory_entry','transfer'
    )),
    target_id TEXT NOT NULL,
    target_revision_id TEXT NOT NULL DEFAULT '',
    target_scope_key TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    ledger_sequence INTEGER NOT NULL CHECK(ledger_sequence >= 1),
    ledger_entry_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('purge_pending','local_purged','purged')),
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE deletion_manifest_entries (
    request_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    artifact_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL DEFAULT '',
    relative_path TEXT,
    content_sha256 TEXT,
    state TEXT NOT NULL CHECK(state IN ('retained','purge_pending','purged')),
    PRIMARY KEY(request_id,ordinal),
    FOREIGN KEY(request_id) REFERENCES deletion_requests(request_id) ON DELETE RESTRICT
);
CREATE TABLE deletion_dependency_blocks (
    request_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK(state IN ('blocked','purge_pending','purged')),
    PRIMARY KEY(request_id,dependency_kind,entity_id,revision_id),
    FOREIGN KEY(request_id) REFERENCES deletion_requests(request_id) ON DELETE RESTRICT
);
CREATE INDEX deletion_dependency_lookup
ON deletion_dependency_blocks(dependency_kind,entity_id,revision_id,state);
CREATE TABLE history_redactions (
    workspace_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    current_revision_id TEXT,
    PRIMARY KEY(workspace_id,entity_kind,entity_id,revision_id),
    FOREIGN KEY(request_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE replica_purge_receipts (
    request_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    system_id TEXT NOT NULL,
    replica_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('purge_pending','purged')),
    receipt_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY(request_id,transfer_id,system_id,replica_id),
    FOREIGN KEY(request_id) REFERENCES deletion_requests(request_id) ON DELETE RESTRICT,
    FOREIGN KEY(transfer_id,system_id,replica_id)
        REFERENCES transfer_replicas(transfer_id,system_id,replica_id) ON DELETE RESTRICT
);
"""


__all__ = ["DELETION_SCHEMA"]
