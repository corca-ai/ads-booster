from typing import Final

SKILL_SCHEMA: Final = """
CREATE TABLE skills (
    workspace_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('builtin_override','agent_created')),
    protected INTEGER NOT NULL CHECK(protected IN (0,1)),
    display_revision_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,skill_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE skill_revisions (
    workspace_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    previous_revision_id TEXT,
    digest TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,skill_id,revision_id),
    FOREIGN KEY(workspace_id,skill_id)
        REFERENCES skills(workspace_id,skill_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,skill_id,previous_revision_id)
        REFERENCES skill_revisions(workspace_id,skill_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE skill_heads (
    workspace_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    head_sequence INTEGER NOT NULL CHECK(head_sequence >= 1),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,skill_id),
    FOREIGN KEY(workspace_id,skill_id,revision_id)
        REFERENCES skill_revisions(workspace_id,skill_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
"""

__all__ = ["SKILL_SCHEMA"]
