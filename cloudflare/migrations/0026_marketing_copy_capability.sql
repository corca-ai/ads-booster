-- `copy.text` produces a candidate artifact, so it uses the same local-artifact effect boundary as
-- native capture. It is bound just like a
-- capture adapter so the worker cannot silently substitute a different copy contract after a
-- creative context was frozen.
INSERT INTO hosted_marketing_adapter_capabilities
    (account_id, capability_id, descriptor_json, descriptor_sha256, effect_class,
     request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state,
     created_at, updated_at)
SELECT account_id, 'copy.text',
       '{"activation_state":"active","capability_id":"copy.text","effect_class":"local_artifact","owner_id":"trace.marketing_copy","receipt_schema_sha256":"368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c","request_schema_sha256":"fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80","schema_version":"trace.adapter-capability.v1"}',
       '832bf83a9d3722daaf6ee93751a655e7753608cc667c3eec5d983bdd0ba39f67',
       'local_artifact', 'fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80',
       '368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c',
       'trace.marketing_copy', 1, 'active', created_at, updated_at
FROM hosted_workspace_accounts
WHERE 1
ON CONFLICT(account_id, capability_id) DO NOTHING;

-- Migration 0024 already provisions capture and registered publication references for a new
-- account.  This independent trigger makes the new local copy adapter available to accounts
-- created after this migration without reopening historical descriptor rows.
CREATE TRIGGER hosted_workspace_account_marketing_copy_capability
AFTER INSERT ON hosted_workspace_accounts
BEGIN
    INSERT INTO hosted_marketing_adapter_capabilities
        (account_id, capability_id, descriptor_json, descriptor_sha256, effect_class,
         request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state,
         created_at, updated_at)
    VALUES (NEW.account_id, 'copy.text',
       '{"activation_state":"active","capability_id":"copy.text","effect_class":"local_artifact","owner_id":"trace.marketing_copy","receipt_schema_sha256":"368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c","request_schema_sha256":"fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80","schema_version":"trace.adapter-capability.v1"}',
       '832bf83a9d3722daaf6ee93751a655e7753608cc667c3eec5d983bdd0ba39f67',
       'local_artifact', 'fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80',
       '368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c',
       'trace.marketing_copy', 1, 'active', NEW.created_at, NEW.updated_at);
END;

-- A binding is evidence of the creative context, never a mutable configuration cache. New
-- requests/manifests must carry it and agree with the context/request that created them.
CREATE TRIGGER hosted_marketing_capability_binding_immutable
BEFORE UPDATE ON hosted_marketing_capability_bindings
BEGIN
    SELECT RAISE(ABORT, 'marketing capability binding is immutable');
END;

CREATE TRIGGER hosted_marketing_artifact_request_requires_bound_capability
BEFORE INSERT ON hosted_marketing_artifact_requests
WHEN NEW.capability_binding_sha256 = ''
   OR NOT EXISTS (
       SELECT 1
       FROM hosted_marketing_creative_treatments AS treatment
       JOIN hosted_marketing_media_plans AS plan ON plan.plan_id = treatment.plan_id
       JOIN hosted_marketing_capability_bindings AS binding
         ON binding.context_receipt_id = plan.context_receipt_id
        AND binding.capability_id = NEW.capability_id
        AND binding.binding_sha256 = NEW.capability_binding_sha256
       WHERE treatment.treatment_id = NEW.treatment_id
         AND treatment.campaign_id = NEW.campaign_id
   )
BEGIN
    SELECT RAISE(ABORT, 'marketing artifact request requires its context capability binding');
END;

CREATE TRIGGER hosted_marketing_artifact_manifest_requires_bound_capability
BEFORE INSERT ON hosted_marketing_artifact_manifests
WHEN NEW.capability_binding_sha256 = ''
   OR NOT EXISTS (
       SELECT 1
       FROM hosted_marketing_artifact_requests AS request
       WHERE request.request_id = NEW.request_id
         AND request.campaign_id = NEW.campaign_id
         AND request.treatment_id = NEW.treatment_id
         AND request.capability_id = json_extract(NEW.manifest_json, '$.capability_id')
         AND request.capability_binding_sha256 = NEW.capability_binding_sha256
   )
BEGIN
    SELECT RAISE(ABORT, 'marketing artifact manifest requires its request capability binding');
END;
