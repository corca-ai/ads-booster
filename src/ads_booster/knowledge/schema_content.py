from typing import Final

CONTENT_SCHEMA: Final = """
CREATE TABLE sources (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    owner_ref TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('file','url','message')),
    source_identity TEXT NOT NULL,
    sanitized_locator TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN (
        'pending','use_only','reference','admit','update','investigate','ignore'
    )),
    admission_revision INTEGER NOT NULL CHECK(admission_revision >= 0),
    visibility TEXT NOT NULL CHECK(visibility IN ('hidden','searchable','blocked')),
    source_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,source_id),
    UNIQUE(scope_key,source_kind,source_identity),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE source_revisions (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
    previous_revision_id TEXT,
    sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    extraction_status TEXT NOT NULL,
    completeness TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    origin_created_at TEXT,
    error_code TEXT,
    revision_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,source_id,revision_id),
    UNIQUE(workspace_id,source_id,revision_number),
    FOREIGN KEY(workspace_id,source_id)
        REFERENCES sources(workspace_id,source_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,source_id,previous_revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE source_heads (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,source_id),
    FOREIGN KEY(workspace_id,source_id,revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE source_files (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    file_kind TEXT NOT NULL CHECK(
        file_kind IN ('original.bin','extracted.md','manifest.json','message.json')),
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    PRIMARY KEY(workspace_id,source_id,revision_id,file_kind),
    FOREIGN KEY(workspace_id,source_id,revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE source_observations (
    workspace_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    final_url TEXT NOT NULL,
    http_status INTEGER NOT NULL CHECK(http_status BETWEEN 100 AND 599),
    etag TEXT,
    last_modified TEXT,
    metadata_sha256 TEXT NOT NULL,
    PRIMARY KEY(workspace_id,observation_id),
    UNIQUE(workspace_id,source_id,revision_id,delivery_id),
    FOREIGN KEY(workspace_id,source_id,revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE segments (
    workspace_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    extraction_version TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    quote_start INTEGER NOT NULL CHECK(quote_start >= 0),
    quote_end INTEGER NOT NULL CHECK(quote_end > quote_start),
    completeness TEXT NOT NULL,
    segment_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,source_id,revision_id,extraction_version,segment_id),
    FOREIGN KEY(workspace_id,source_id,revision_id)
        REFERENCES source_revisions(workspace_id,source_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE conversation_events (
    workspace_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    event_kind TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    scope_key TEXT NOT NULL,
    speaker_ref TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,conversation_id,message_id,revision,event_kind),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE conversation_curation_cursors (
    workspace_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    completed_sequence INTEGER NOT NULL CHECK(completed_sequence >= 0),
    completed_revision INTEGER NOT NULL CHECK(completed_revision >= 0),
    pending_fence_state TEXT NOT NULL CHECK(pending_fence_state IN ('clear','pending','blocked')),
    PRIMARY KEY(workspace_id,conversation_id,scope_key),
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE wiki_pages (
    workspace_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','redirect','split','retracted')),
    page_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,page_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE knowledge_revisions (
    workspace_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    previous_revision_id TEXT,
    body_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    attributes_sha256 TEXT NOT NULL,
    revision_json TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,page_id,revision_id),
    FOREIGN KEY(workspace_id,page_id)
        REFERENCES wiki_pages(workspace_id,page_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,page_id,previous_revision_id)
        REFERENCES knowledge_revisions(workspace_id,page_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE knowledge_heads (
    workspace_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    head_sequence INTEGER NOT NULL CHECK(head_sequence >= 1),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,page_id),
    FOREIGN KEY(workspace_id,page_id,revision_id)
        REFERENCES knowledge_revisions(workspace_id,page_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE page_aliases (
    workspace_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(workspace_id,page_id,revision_id,normalized_alias),
    FOREIGN KEY(workspace_id,page_id,revision_id)
        REFERENCES knowledge_revisions(workspace_id,page_id,revision_id) ON DELETE RESTRICT
);
CREATE INDEX page_alias_lookup ON page_aliases(workspace_id,normalized_alias);
CREATE TABLE page_relations (
    workspace_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    from_page_id TEXT NOT NULL,
    from_revision_id TEXT NOT NULL,
    to_page_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('related_to','part_of','summarizes','split_into')),
    relation_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,relation_id,from_revision_id),
    FOREIGN KEY(workspace_id,from_page_id,from_revision_id)
        REFERENCES knowledge_revisions(workspace_id,page_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,to_page_id)
        REFERENCES wiki_pages(workspace_id,page_id) ON DELETE RESTRICT
);
CREATE TABLE page_redirects (
    workspace_id TEXT NOT NULL,
    from_page_id TEXT NOT NULL,
    to_page_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,from_page_id),
    CHECK(from_page_id != to_page_id),
    FOREIGN KEY(workspace_id,from_page_id)
        REFERENCES wiki_pages(workspace_id,page_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,to_page_id)
        REFERENCES wiki_pages(workspace_id,page_id) ON DELETE RESTRICT
);
CREATE TABLE claims (
    workspace_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('fact','decision','inference')),
    PRIMARY KEY(workspace_id,claim_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT
);
CREATE TABLE claim_versions (
    workspace_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','contested','superseded','retracted')),
    statement_sha256 TEXT NOT NULL,
    applicability_sha256 TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    claim_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,claim_id,revision_id),
    FOREIGN KEY(workspace_id,claim_id) REFERENCES claims(workspace_id,claim_id) ON DELETE RESTRICT
);
CREATE TABLE claim_locations (
    workspace_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim_revision_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_revision_id TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
    PRIMARY KEY(workspace_id,claim_id,claim_revision_id,page_id,page_revision_id),
    FOREIGN KEY(workspace_id,claim_id,claim_revision_id)
        REFERENCES claim_versions(workspace_id,claim_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,page_id,page_revision_id)
        REFERENCES knowledge_revisions(workspace_id,page_id,revision_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX claim_current_location
ON claim_locations(workspace_id,claim_id) WHERE is_current=1;
CREATE TABLE evidence_nodes (
    node_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    entity_kind TEXT NOT NULL CHECK(
        entity_kind IN ('source_segment','conversation_event','memory_entry','claim')),
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    segment_id TEXT NOT NULL DEFAULT '',
    UNIQUE(workspace_id,entity_kind,entity_id,revision_id,segment_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE evidence_edges (
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    edge_kind TEXT NOT NULL CHECK(edge_kind IN ('supports','counters','derived_from','quotes')),
    quote_sha256 TEXT,
    PRIMARY KEY(from_node_id,to_node_id,edge_kind),
    FOREIGN KEY(from_node_id) REFERENCES evidence_nodes(node_id) ON DELETE RESTRICT,
    FOREIGN KEY(to_node_id) REFERENCES evidence_nodes(node_id) ON DELETE RESTRICT
);
CREATE INDEX evidence_reverse ON evidence_edges(to_node_id,from_node_id);
CREATE TABLE evidence_dependency_invalidations (
    workspace_id TEXT NOT NULL,
    upstream_kind TEXT NOT NULL CHECK(upstream_kind='memory_entry'),
    upstream_id TEXT NOT NULL,
    upstream_revision_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    resulting_state TEXT NOT NULL CHECK(resulting_state IN ('stale','restricted')),
    reason TEXT NOT NULL,
    PRIMARY KEY(
        workspace_id,upstream_kind,upstream_id,upstream_revision_id,operation_id
    ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id) ON DELETE RESTRICT
);
CREATE TABLE claim_visibility_fences (
    workspace_id TEXT NOT NULL,
    dependent_claim_id TEXT NOT NULL,
    dependent_revision_id TEXT NOT NULL,
    upstream_kind TEXT NOT NULL CHECK(upstream_kind='memory_entry'),
    upstream_id TEXT NOT NULL,
    upstream_revision_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    resulting_state TEXT NOT NULL CHECK(resulting_state IN ('stale','restricted')),
    reason TEXT NOT NULL,
    PRIMARY KEY(
        workspace_id,dependent_claim_id,dependent_revision_id,
        upstream_kind,upstream_id,upstream_revision_id,operation_id
    ),
    FOREIGN KEY(workspace_id,dependent_claim_id,dependent_revision_id)
        REFERENCES claim_versions(workspace_id,claim_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(
        workspace_id,upstream_kind,upstream_id,upstream_revision_id,operation_id
    ) REFERENCES evidence_dependency_invalidations(
        workspace_id,upstream_kind,upstream_id,upstream_revision_id,operation_id
    ) ON DELETE RESTRICT
);
CREATE INDEX claim_visibility_current
ON claim_visibility_fences(workspace_id,dependent_claim_id,dependent_revision_id);
CREATE TABLE memory_documents (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('team','soul','core','daily')),
    brand_id TEXT,
    local_date TEXT,
    timezone TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    document_json TEXT NOT NULL,
    CHECK((kind='soul' AND brand_id IS NOT NULL AND local_date IS NULL)
        OR (kind='daily' AND brand_id IS NULL AND local_date IS NOT NULL)
        OR (kind IN ('team','core') AND brand_id IS NULL AND local_date IS NULL)),
    PRIMARY KEY(workspace_id,document_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,brand_id) REFERENCES brands(workspace_id,brand_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX memory_team_core_identity
ON memory_documents(workspace_id,kind) WHERE kind IN ('team','core');
CREATE UNIQUE INDEX memory_soul_identity
ON memory_documents(workspace_id,kind,brand_id) WHERE kind='soul';
CREATE UNIQUE INDEX memory_daily_identity
ON memory_documents(workspace_id,kind,local_date) WHERE kind='daily';
CREATE TABLE memory_revisions (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    previous_revision_id TEXT,
    body_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    revision_json TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,document_id,revision_id),
    FOREIGN KEY(workspace_id,document_id)
        REFERENCES memory_documents(workspace_id,document_id) ON DELETE RESTRICT,
    FOREIGN KEY(workspace_id,document_id,previous_revision_id)
        REFERENCES memory_revisions(workspace_id,document_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE memory_heads (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    head_sequence INTEGER NOT NULL CHECK(head_sequence >= 1),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(workspace_id,document_id),
    FOREIGN KEY(workspace_id,document_id,revision_id)
        REFERENCES memory_revisions(workspace_id,document_id,revision_id) ON DELETE RESTRICT
);
CREATE TABLE memory_entries (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    memory_revision_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    dependency_state TEXT NOT NULL CHECK(dependency_state IN ('current','stale','restricted')),
    origin TEXT NOT NULL CHECK(origin IN ('direct','wiki_summary')),
    usage_role TEXT NOT NULL CHECK(usage_role IN ('reference','constraint')),
    soul_section TEXT,
    scope_key TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,document_id,memory_revision_id,entry_id),
    FOREIGN KEY(workspace_id,document_id,memory_revision_id)
        REFERENCES memory_revisions(workspace_id,document_id,revision_id) ON DELETE RESTRICT,
    FOREIGN KEY(scope_key) REFERENCES access_scopes(scope_key) ON DELETE RESTRICT
);
CREATE TABLE derived_memory_refs (
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    memory_revision_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    upstream_kind TEXT NOT NULL CHECK(upstream_kind IN ('source','wiki_claim','memory_entry')),
    upstream_id TEXT NOT NULL,
    upstream_revision_id TEXT NOT NULL,
    semantic_fingerprint TEXT,
    PRIMARY KEY(workspace_id,document_id,memory_revision_id,entry_id,upstream_kind,upstream_id),
    FOREIGN KEY(workspace_id,document_id,memory_revision_id,entry_id)
        REFERENCES memory_entries(workspace_id,document_id,memory_revision_id,entry_id)
        ON DELETE RESTRICT
);
CREATE INDEX derived_memory_reverse
ON derived_memory_refs(workspace_id,upstream_kind,upstream_id,upstream_revision_id);
CREATE TABLE constraint_bindings (
    workspace_id TEXT NOT NULL,
    constraint_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    memory_revision_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    authority_class TEXT NOT NULL,
    subject_key TEXT,
    compatibility TEXT NOT NULL CHECK(
        compatibility IN ('compatible','conflict','compatibility_pending')),
    binding_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,constraint_id,memory_revision_id),
    FOREIGN KEY(workspace_id,document_id,memory_revision_id,entry_id)
        REFERENCES memory_entries(workspace_id,document_id,memory_revision_id,entry_id)
        ON DELETE RESTRICT
);
"""

__all__ = ["CONTENT_SCHEMA"]
