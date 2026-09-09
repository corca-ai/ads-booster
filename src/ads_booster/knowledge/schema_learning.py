from typing import Final

LEARNING_SCHEMA: Final = """
CREATE TABLE learning_counters (
    workspace_id TEXT PRIMARY KEY,
    counter_json TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE learning_rounds (
    round_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK(
        purpose IN ('conversational_feedback','terminal_experience_review')
    ),
    state TEXT NOT NULL CHECK(state IN ('collecting','ready','running','completed','cancelled')),
    created_at TEXT NOT NULL,
    round_json TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE INDEX learning_rounds_workspace ON learning_rounds(workspace_id,created_at);
CREATE TABLE learning_admissions (
    admission_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    admission_kind TEXT NOT NULL CHECK(admission_kind IN ('turn','experience')),
    watermark TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    event_json TEXT,
    experience_json TEXT,
    occurred_at TEXT NOT NULL,
    sealed_round_id TEXT,
    invalidated INTEGER NOT NULL DEFAULT 0 CHECK(invalidated IN (0,1)),
    UNIQUE(workspace_id,admission_kind,watermark),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(batch_id) REFERENCES curation_batches(batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(sealed_round_id) REFERENCES learning_rounds(round_id) ON DELETE RESTRICT
);
CREATE INDEX learning_admissions_pending
    ON learning_admissions(workspace_id,sealed_round_id,invalidated,occurred_at);
CREATE TABLE learning_batch_partitions (
    batch_id TEXT PRIMARY KEY,
    partition_key TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    FOREIGN KEY(batch_id) REFERENCES curation_batches(batch_id) ON DELETE RESTRICT
);
CREATE INDEX learning_batch_partition_active
    ON learning_batch_partitions(partition_key,batch_id);
CREATE TABLE learning_consumed_targets (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,source_id,source_revision_id,target_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
"""

__all__ = ["LEARNING_SCHEMA"]
