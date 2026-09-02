import assert from "node:assert/strict";
import test from "node:test";

import { prepareMarketingCaptureManifests } from "../src/hosted-capture-manifests.js";
import {
  canonicalJson,
  canonicalSha256,
  MarketingCapabilityError,
} from "../src/marketing-adapter-capabilities.js";

async function fixture(requestState = "approved") {
  const descriptor = {
    activation_state: "active",
    capability_id: "capture.native_png",
    effect_class: "local_artifact",
    owner_id: "trace.native_capture",
    receipt_schema_sha256: "b".repeat(64),
    request_schema_sha256: "a".repeat(64),
    schema_version: "trace.adapter-capability.v1",
  };
  const descriptorSha256 = await canonicalSha256(descriptor);
  const binding = {
    capability_id: descriptor.capability_id,
    descriptor_sha256: descriptorSha256,
    effect_class: descriptor.effect_class,
    request_schema_sha256: descriptor.request_schema_sha256,
    receipt_schema_sha256: descriptor.receipt_schema_sha256,
    owner_id: descriptor.owner_id,
  };
  const row = {
    assignment_id: "assignment-1",
    campaign_id: "campaign-1",
    treatment_id: "treatment-1",
    request_id: "request-capture-1",
    capability_id: "capture.native_png",
    request_json: canonicalJson({ claim_ids: ["claim-installed"] }),
    request_sha256: "c".repeat(64),
    capability_binding_sha256: await canonicalSha256(binding),
    request_state: requestState,
  };
  const catalog = {
    capability_id: descriptor.capability_id,
    descriptor_json: canonicalJson(descriptor),
    descriptor_sha256: descriptorSha256,
    effect_class: descriptor.effect_class,
    request_schema_sha256: descriptor.request_schema_sha256,
    receipt_schema_sha256: descriptor.receipt_schema_sha256,
    owner_id: descriptor.owner_id,
    enabled: 1,
    activation_state: "active",
  };
  return {
    env: {
      DB: {
        prepare(sql) {
          return {
            bind() {
              return {
                async all() {
                  if (sql.includes("FROM hosted_workspace_candidates AS candidate")) {
                    return { results: [row] };
                  }
                  if (sql.includes("FROM hosted_marketing_adapter_capabilities")) {
                    return { results: [catalog] };
                  }
                  throw new Error(`unexpected SQL: ${sql}`);
                },
              };
            },
          };
        },
      },
    },
    row,
  };
}

test("capture manifest retry keeps the immutable task timestamp and digest input", async () => {
  const { env, row } = await fixture("approved");
  const task = {
    task_id: "capture-task-1",
    account_id: "trace_kr",
    candidate_id: "candidate-1",
    created_at: "2000-01-01T00:00:00.000Z",
  };
  const output = {
    capture_source: "native_appium",
    artifact_role: "trace_wallpaper",
    image_postprocess_source: "none",
    native_image_sha256: "d".repeat(64),
  };
  const first = await prepareMarketingCaptureManifests(
    env,
    task,
    "workspace/image.png",
    "d".repeat(64),
    output,
  );
  row.request_state = "succeeded";
  const retry = await prepareMarketingCaptureManifests(
    env,
    task,
    "workspace/image.png",
    "d".repeat(64),
    output,
  );

  assert.deepEqual(retry, first);
  assert.equal(first[0].created_at, task.created_at);
  assert.equal(await canonicalSha256(first[0]), await canonicalSha256(retry[0]));
});

test("capture manifest preparation fails closed for a stale request", async () => {
  const { env } = await fixture("stale");
  await assert.rejects(
    prepareMarketingCaptureManifests(
      env,
      {
        task_id: "capture-task-1",
        account_id: "trace_kr",
        candidate_id: "candidate-1",
        created_at: "2000-01-01T00:00:00.000Z",
      },
      "workspace/image.png",
      "d".repeat(64),
      {
        capture_source: "native_appium",
        artifact_role: "trace_wallpaper",
        image_postprocess_source: "none",
        native_image_sha256: "d".repeat(64),
      },
    ),
    MarketingCapabilityError,
  );
});
