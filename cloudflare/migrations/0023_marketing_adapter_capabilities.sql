-- Account-scoped installations are the transitional tenant boundary.  A descriptor is immutable:
-- changing an adapter contract creates a new digest and affects only newly bound contexts.
CREATE TABLE hosted_marketing_adapter_capabilities (
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    capability_id TEXT NOT NULL,
    descriptor_json TEXT NOT NULL,
    descriptor_sha256 TEXT NOT NULL CHECK (length(descriptor_sha256) = 64),
    effect_class TEXT NOT NULL CHECK (effect_class IN ('none', 'local_artifact', 'external')),
    request_schema_sha256 TEXT NOT NULL CHECK (length(request_schema_sha256) = 64),
    receipt_schema_sha256 TEXT NOT NULL CHECK (length(receipt_schema_sha256) = 64),
    owner_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    activation_state TEXT NOT NULL CHECK (activation_state IN ('active', 'registered_reference')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, capability_id)
);

CREATE TABLE hosted_marketing_capability_bindings (
    context_receipt_id TEXT NOT NULL REFERENCES hosted_marketing_context_receipts(receipt_id)
        ON DELETE CASCADE,
    capability_id TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL CHECK (length(binding_sha256) = 64),
    descriptor_sha256 TEXT NOT NULL CHECK (length(descriptor_sha256) = 64),
    effect_class TEXT NOT NULL CHECK (effect_class IN ('none', 'local_artifact', 'external')),
    request_schema_sha256 TEXT NOT NULL CHECK (length(request_schema_sha256) = 64),
    receipt_schema_sha256 TEXT NOT NULL CHECK (length(receipt_schema_sha256) = 64),
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (context_receipt_id, capability_id),
    UNIQUE (context_receipt_id, binding_sha256)
);

ALTER TABLE hosted_marketing_artifact_requests
    ADD COLUMN capability_binding_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE hosted_marketing_artifact_manifests
    ADD COLUMN capability_binding_sha256 TEXT NOT NULL DEFAULT '';

CREATE INDEX hosted_marketing_capability_bindings_receipt
ON hosted_marketing_capability_bindings (context_receipt_id, capability_id);

-- Seed the existing Trace installations.  New-account provisioning uses the same immutable
-- descriptors in the runtime resolver; this migration preserves accounts that predate the catalog.
INSERT INTO hosted_marketing_adapter_capabilities
    (account_id, capability_id, descriptor_json, descriptor_sha256, effect_class,
     request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state,
     created_at, updated_at)
SELECT account_id, 'capture.native_png',
       '{"activation_state":"active","capability_id":"capture.native_png","effect_class":"local_artifact","owner_id":"trace.native_capture","receipt_schema_sha256":"368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c","request_schema_sha256":"fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80","schema_version":"trace.adapter-capability.v1"}',
       'aefd9c88b195f8bb98a3db8974d3bbcbbfd7e510552725b85013ca6fabc31b82',
       'local_artifact', 'fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80',
       '368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c',
       'trace.native_capture', 1, 'active', created_at, updated_at
FROM hosted_workspace_accounts
WHERE 1
ON CONFLICT(account_id, capability_id) DO NOTHING;

INSERT INTO hosted_marketing_adapter_capabilities
    (account_id, capability_id, descriptor_json, descriptor_sha256, effect_class,
     request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state,
     created_at, updated_at)
SELECT account_id, 'publish.threads',
       '{"activation_state":"registered_reference","capability_id":"publish.threads","effect_class":"external","owner_id":"threads.publisher","receipt_schema_sha256":"368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c","request_schema_sha256":"fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80","schema_version":"trace.adapter-capability.v1"}',
       'f4ce306e0b4d6c1a67d8bb773ddf911c79bb8bc353a7db3fae99212067fca7c3',
       'external', 'fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80',
       '368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c',
       'threads.publisher', 1, 'registered_reference', created_at, updated_at
FROM hosted_workspace_accounts
WHERE 1
ON CONFLICT(account_id, capability_id) DO NOTHING;
