-- Customer intelligence is normalized and reviewable evidence, not mutable transcript memory.
-- v1 accepts only a manually reviewed normalization; connector, retrieval, and raw-transcript
-- ingestion remain deliberately outside this migration.
CREATE TABLE hosted_marketing_customer_signals (
    signal_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.customer-signal.v1'),
    source_kind TEXT NOT NULL CHECK (source_kind = 'manual_normalized'),
    source_ref TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    audience_segment_id TEXT NOT NULL,
    signal_kind TEXT NOT NULL CHECK (
        signal_kind IN ('need', 'objection', 'desired_outcome', 'audience_language', 'behavior')
    ),
    consent_status TEXT NOT NULL CHECK (consent_status = 'confirmed'),
    confidence_basis_points INTEGER NOT NULL CHECK (confidence_basis_points BETWEEN 0 AND 10000),
    observed_at TEXT NOT NULL,
    fresh_until TEXT NOT NULL,
    retention_until TEXT NOT NULL,
    review_state TEXT NOT NULL CHECK (review_state IN ('pending', 'approved', 'rejected')),
    reviewer_id TEXT,
    reviewed_at TEXT,
    signal_json TEXT NOT NULL CHECK (
        json_type(signal_json, '$.raw_transcript') IS NULL
        AND json_type(signal_json, '$.transcript') IS NULL
        AND json_type(signal_json, '$.instructions') IS NULL
    ),
    signal_sha256 TEXT NOT NULL CHECK (length(signal_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, signal_id),
    UNIQUE (account_id, signal_sha256),
    CHECK (observed_at <= fresh_until AND fresh_until <= retention_until),
    CHECK (
        (review_state = 'pending' AND reviewer_id IS NULL AND reviewed_at IS NULL)
        OR (review_state IN ('approved', 'rejected') AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX hosted_marketing_customer_signals_account
ON hosted_marketing_customer_signals (account_id, review_state, fresh_until DESC);

CREATE TRIGGER hosted_marketing_customer_signal_payload_immutable
BEFORE UPDATE OF account_id, schema_version, source_kind, source_ref, source_sha256,
                 audience_segment_id, signal_kind, consent_status, confidence_basis_points,
                 observed_at, fresh_until, retention_until, signal_json, signal_sha256
ON hosted_marketing_customer_signals
BEGIN
    SELECT RAISE(ABORT, 'customer signal payload is immutable');
END;

CREATE TRIGGER hosted_marketing_customer_signal_review_final
BEFORE UPDATE OF review_state, reviewer_id, reviewed_at ON hosted_marketing_customer_signals
WHEN OLD.review_state != 'pending'
BEGIN
    SELECT RAISE(ABORT, 'customer signal review is final');
END;

CREATE TABLE hosted_marketing_context_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.marketing-context.v1'),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, snapshot_id),
    UNIQUE (account_id, snapshot_sha256),
    CHECK (approved_at < expires_at)
);

CREATE TABLE hosted_marketing_context_snapshot_signals (
    snapshot_id TEXT NOT NULL REFERENCES hosted_marketing_context_snapshots(snapshot_id)
        ON DELETE CASCADE,
    signal_id TEXT NOT NULL REFERENCES hosted_marketing_customer_signals(signal_id)
        ON DELETE RESTRICT,
    signal_sha256 TEXT NOT NULL CHECK (length(signal_sha256) = 64),
    PRIMARY KEY (snapshot_id, signal_id)
);

CREATE INDEX hosted_marketing_context_snapshots_account
ON hosted_marketing_context_snapshots (account_id, expires_at DESC);

CREATE TRIGGER hosted_marketing_context_snapshot_immutable
BEFORE UPDATE ON hosted_marketing_context_snapshots
BEGIN
    SELECT RAISE(ABORT, 'marketing context snapshot is immutable');
END;

CREATE TRIGGER hosted_marketing_context_snapshot_signal_scope
BEFORE INSERT ON hosted_marketing_context_snapshot_signals
WHEN NOT EXISTS (
    SELECT 1
    FROM hosted_marketing_context_snapshots AS snapshot
    JOIN hosted_marketing_customer_signals AS signal
      ON signal.account_id = snapshot.account_id
     AND signal.signal_id = NEW.signal_id
     AND signal.signal_sha256 = NEW.signal_sha256
     AND signal.review_state = 'approved'
     AND signal.consent_status = 'confirmed'
     AND signal.fresh_until >= snapshot.expires_at
     AND signal.retention_until >= snapshot.expires_at
    WHERE snapshot.snapshot_id = NEW.snapshot_id
)
BEGIN
    SELECT RAISE(ABORT, 'marketing context snapshot signal is invalid');
END;

CREATE TRIGGER hosted_marketing_context_snapshot_signal_immutable
BEFORE UPDATE ON hosted_marketing_context_snapshot_signals
BEGIN
    SELECT RAISE(ABORT, 'marketing context snapshot signal is immutable');
END;

ALTER TABLE hosted_marketing_campaigns ADD COLUMN marketing_context_snapshot_id TEXT;
ALTER TABLE hosted_marketing_campaigns ADD COLUMN marketing_context_snapshot_sha256 TEXT;

CREATE TRIGGER hosted_marketing_campaign_context_insert
BEFORE INSERT ON hosted_marketing_campaigns
WHEN (NEW.marketing_context_snapshot_id IS NULL) != (NEW.marketing_context_snapshot_sha256 IS NULL)
  OR (
      NEW.marketing_context_snapshot_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM hosted_marketing_context_snapshots AS snapshot
          WHERE snapshot.snapshot_id = NEW.marketing_context_snapshot_id
            AND snapshot.account_id = NEW.account_id
            AND snapshot.snapshot_sha256 = NEW.marketing_context_snapshot_sha256
            AND snapshot.expires_at > NEW.created_at
      )
  )
BEGIN
    SELECT RAISE(ABORT, 'marketing campaign context snapshot is invalid');
END;

CREATE TRIGGER hosted_marketing_campaign_context_immutable
BEFORE UPDATE OF marketing_context_snapshot_id, marketing_context_snapshot_sha256
ON hosted_marketing_campaigns
WHEN OLD.marketing_context_snapshot_id IS NOT NEW.marketing_context_snapshot_id
  OR OLD.marketing_context_snapshot_sha256 IS NOT NEW.marketing_context_snapshot_sha256
BEGIN
    SELECT RAISE(ABORT, 'marketing campaign context snapshot is immutable');
END;
