ALTER TABLE hosted_marketing_reference_snapshots
ADD COLUMN verification_bundle_json TEXT;

ALTER TABLE hosted_marketing_reference_snapshots
ADD COLUMN verification_bundle_sha256 TEXT
    CHECK (verification_bundle_sha256 IS NULL OR length(verification_bundle_sha256) = 64);

CREATE TABLE hosted_marketing_reference_source_receipts (
    receipt_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES hosted_marketing_reference_snapshots(snapshot_id)
        ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = 'trace.reference-source-receipt.v1'),
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    byte_length INTEGER NOT NULL CHECK (byte_length BETWEEN 1 AND 1048576),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
    fetched_at TEXT NOT NULL,
    UNIQUE (snapshot_id, source_id)
);

CREATE TRIGGER hosted_marketing_reference_source_receipts_immutable
BEFORE UPDATE ON hosted_marketing_reference_source_receipts
BEGIN
    SELECT RAISE(ABORT, 'marketing reference source receipts are immutable');
END;
