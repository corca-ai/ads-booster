import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  assertCreativeCapabilitySnapshot,
  canonicalJson,
  MarketingCapabilityError,
  resolveCreativeCapabilityBindings,
  validateCreativeCapabilitySnapshot,
} from "../src/marketing-adapter-capabilities.js";

function digest(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function catalogRows({ copyEnabled = 1, tamperCaptureDescriptor = false } = {}) {
  return [
    ["capture.native_png", "trace.native_capture", 1],
    ["copy.text", "trace.marketing_copy", copyEnabled],
  ].map(([capability_id, owner_id, enabled]) => {
    const descriptor = {
      schema_version: "trace.adapter-capability.v1",
      capability_id,
      effect_class: "local_artifact",
      owner_id,
      request_schema_sha256: "a".repeat(64),
      receipt_schema_sha256: "b".repeat(64),
      activation_state: "active",
    };
    return {
      capability_id,
      descriptor_json: canonicalJson(descriptor),
      descriptor_sha256: tamperCaptureDescriptor && capability_id === "capture.native_png"
        ? "0".repeat(64)
        : digest(descriptor),
      effect_class: descriptor.effect_class,
      request_schema_sha256: descriptor.request_schema_sha256,
      receipt_schema_sha256: descriptor.receipt_schema_sha256,
      owner_id,
      enabled,
      activation_state: "active",
    };
  });
}

function catalogDb(rows) {
  return {
    prepare(sql) {
      return {
        bind() {
          return {
            async all() {
              assert.match(sql, /hosted_marketing_adapter_capabilities/);
              return { results: rows };
            },
          };
        },
      };
    },
  };
}

async function payloadFor(rows) {
  const bindings = await resolveCreativeCapabilityBindings(catalogDb(rows), "trace_kr");
  return {
    available_capabilities: bindings.map((binding) => binding.capability_id),
    capability_bindings: bindings,
    capability_snapshot_sha256: digest({ capability_bindings: bindings }),
  };
}

test("creative planning freezes server-derived descriptor bindings", async () => {
  const rows = catalogRows();
  const bindings = await resolveCreativeCapabilityBindings(catalogDb(rows), "trace_kr");

  assert.deepEqual(bindings.map((binding) => binding.capability_id), [
    "capture.native_png",
    "copy.text",
  ]);
  assert.equal(bindings[0].binding_sha256, digest({
    capability_id: "capture.native_png",
    descriptor_sha256: rows[0].descriptor_sha256,
    effect_class: "local_artifact",
    request_schema_sha256: "a".repeat(64),
    receipt_schema_sha256: "b".repeat(64),
    owner_id: "trace.native_capture",
  }));
});

test("creative callback fails closed if a frozen adapter was revoked or changed", async () => {
  const activeRows = catalogRows();
  const payload = await payloadFor(activeRows);

  await assert.doesNotReject(
    assertCreativeCapabilitySnapshot(catalogDb(activeRows), "trace_kr", payload),
  );
  await assert.rejects(
    assertCreativeCapabilitySnapshot(catalogDb(catalogRows({ copyEnabled: 0 })), "trace_kr", payload),
    (error) => error instanceof MarketingCapabilityError
      && /not active|catalog changed/.test(error.message),
  );
});

test("descriptor or wire-binding tampering never becomes a valid capability snapshot", async () => {
  await assert.rejects(
    resolveCreativeCapabilityBindings(catalogDb(catalogRows({ tamperCaptureDescriptor: true })), "trace_kr"),
    (error) => error instanceof MarketingCapabilityError && /descriptor digest/.test(error.message),
  );
  const payload = await payloadFor(catalogRows());
  payload.capability_bindings[1].binding_sha256 = "f".repeat(64);

  await assert.rejects(
    validateCreativeCapabilitySnapshot(payload),
    (error) => error instanceof MarketingCapabilityError && /binding digest/.test(error.message),
  );
});
