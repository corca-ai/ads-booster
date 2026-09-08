from typing import Final

WORK_SCHEMA: Final = """
CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('applied','rejected','failed')),
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE operation_heads (
    operation_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('source','page','memory','brand')),
    entity_id TEXT NOT NULL,
    expected_revision_id TEXT,
    resulting_revision_id TEXT NOT NULL,
    PRIMARY KEY(operation_id,entity_kind,entity_id),
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE operation_records (
    operation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    record_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(operation_id,ordinal),
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'extraction','curation','index','memory_consolidate','memory_summary_refresh',
        'memory_view_refresh','source_review'
    )),
    state TEXT NOT NULL CHECK(state IN (
        'queued','running','waiting_dependency','awaiting_answer','completed','failed','cancelled'
    )),
    priority TEXT NOT NULL CHECK(priority IN ('urgent','routine')),
    unique_key TEXT NOT NULL,
    batch_id TEXT,
    root_event_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reason_code TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    lease_generation INTEGER NOT NULL CHECK(lease_generation >= 0),
    result_sha256 TEXT,
    job_json TEXT NOT NULL,
    UNIQUE(workspace_id,kind,unique_key),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE INDEX jobs_ready ON jobs(state,priority,due_at,job_id);
CREATE TABLE knowledge_job_requests (
    job_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    request_json TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE knowledge_questions (
    workspace_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','answered','cancelled')),
    question_json TEXT NOT NULL,
    answer_event_id TEXT,
    created_at TEXT NOT NULL,
    answered_at TEXT,
    PRIMARY KEY(workspace_id,question_id),
    CHECK(
        (status='answered' AND answer_event_id IS NOT NULL AND answered_at IS NOT NULL)
        OR (status IN ('pending','cancelled') AND answer_event_id IS NULL AND answered_at IS NULL)
    ),
    CHECK(answered_at IS NULL OR answered_at >= created_at),
    CHECK(json_extract(question_json,'$.workspace_id') IS workspace_id),
    CHECK(json_extract(question_json,'$.question_id') IS question_id),
    CHECK(json_extract(question_json,'$.actor_ref') IS actor_ref),
    CHECK(json_extract(question_json,'$.status') IS status),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,actor_ref)
        REFERENCES members(workspace_id,actor_id) ON DELETE RESTRICT
);
CREATE TRIGGER knowledge_questions_answer_event_insert
BEFORE INSERT ON knowledge_questions
WHEN NEW.answer_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_events
    WHERE workspace_id=NEW.workspace_id AND message_id=NEW.answer_event_id
)
BEGIN
    SELECT RAISE(ABORT,'knowledge_question_answer_event_not_found');
END;
CREATE TRIGGER knowledge_questions_answer_event_update
BEFORE UPDATE OF answer_event_id ON knowledge_questions
WHEN NEW.answer_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_events
    WHERE workspace_id=NEW.workspace_id AND message_id=NEW.answer_event_id
)
BEGIN
    SELECT RAISE(ABORT,'knowledge_question_answer_event_not_found');
END;
CREATE TABLE explicit_adoption_receipts (
    workspace_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    expected_revision_id TEXT NOT NULL,
    authenticated_event_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    actor_ref TEXT GENERATED ALWAYS AS (
        json_extract(receipt_json,'$.actor_ref')
    ) STORED NOT NULL,
    PRIMARY KEY(workspace_id,receipt_id),
    UNIQUE(workspace_id,question_id),
    UNIQUE(workspace_id,proposal_id),
    CHECK(json_extract(receipt_json,'$.workspace_id') IS workspace_id),
    CHECK(json_extract(receipt_json,'$.receipt_id') IS receipt_id),
    CHECK(json_extract(receipt_json,'$.question_id') IS question_id),
    CHECK(json_extract(receipt_json,'$.proposal_id') IS proposal_id),
    CHECK(json_extract(receipt_json,'$.brand_id') IS brand_id),
    CHECK(json_extract(receipt_json,'$.expected_revision_id') IS expected_revision_id),
    CHECK(json_extract(receipt_json,'$.authenticated_event_id') IS authenticated_event_id),
    FOREIGN KEY(workspace_id,question_id)
        REFERENCES knowledge_questions(workspace_id,question_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,actor_ref)
        REFERENCES members(workspace_id,actor_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id)
        REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT
);
CREATE TRIGGER explicit_adoption_receipts_trusted_answer_insert
BEFORE INSERT ON explicit_adoption_receipts
WHEN NOT EXISTS (
    SELECT 1 FROM knowledge_questions
    WHERE workspace_id=NEW.workspace_id
        AND question_id=NEW.question_id
        AND actor_ref=NEW.actor_ref
        AND status='answered'
        AND answer_event_id=NEW.authenticated_event_id
        AND answered_at=NEW.created_at
        AND json_extract(question_json,'$.pending_proposal.proposal_id')=NEW.proposal_id
        AND json_extract(question_json,'$.pending_proposal.target_kind')='soul'
        AND json_extract(question_json,'$.pending_proposal.brand_id')=NEW.brand_id
        AND json_extract(question_json,'$.pending_proposal.expected_revision_id')
            =NEW.expected_revision_id
)
BEGIN
    SELECT RAISE(ABORT,'explicit_adoption_receipt_requires_trusted_answer');
END;
CREATE TABLE curation_batches (
    batch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('urgent','routine')),
    policy_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('collecting','ready','running','completed','cancelled')),
    first_event_at TEXT NOT NULL,
    batch_deadline TEXT NOT NULL,
    batch_json TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE batch_items (
    batch_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_revision INTEGER NOT NULL CHECK(event_revision >= 1),
    result_status TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY(batch_id,event_id,event_revision),
    FOREIGN KEY(batch_id) REFERENCES curation_batches(batch_id) ON DELETE RESTRICT
);
CREATE TABLE index_outbox (
    item_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('source','page','memory')),
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    extraction_version TEXT,
    admission_revision INTEGER,
    indexer_version TEXT NOT NULL,
    unique_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed')),
    lease_owner TEXT,
    lease_expires_at TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
    error_code TEXT,
    UNIQUE(workspace_id,unique_key),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE INDEX index_outbox_pending ON index_outbox(state,item_id);
CREATE TABLE memory_view_outbox (
    item_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK(view_kind IN ('team','soul','core','daily')),
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
CREATE INDEX memory_view_outbox_pending ON memory_view_outbox(state,item_id);
CREATE TABLE delivery_receipts (
    workspace_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    index_item_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,delivery_id),
    FOREIGN KEY(workspace_id,source_id,source_revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE RESTRICT,
    FOREIGN KEY(index_item_id) REFERENCES index_outbox(item_id) ON DELETE RESTRICT
);
CREATE TABLE tombstones (
    tombstone_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_revision_id TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('blocked','purge_pending','purged')),
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id,target_kind,target_id,target_revision_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    chunker_version TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, content, tokenize='unicode61');
"""

__all__ = ["WORK_SCHEMA"]
