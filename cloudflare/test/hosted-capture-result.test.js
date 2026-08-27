import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  assertHostedCaptureLinkage,
  HostedCaptureResultError,
  prepareHostedCaptureResult,
} from "../src/hosted-capture-result.js";

const image = Buffer.from("\x89PNG\r\n\x1a\ntrace-native-image", "binary");
const digest = createHash("sha256").update(image).digest("hex");

function result(overrides = {}) {
  return {
    status: "succeeded",
    output: {
      content_type: "image/png",
      capture_source: "native_appium",
      native_export_binding_verified: true,
      image_base64: image.toString("base64"),
      image_sha256: digest,
      ...overrides,
    },
  };
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(
      (key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

function generationProvenance() {
  const plan = {
    schema_version: "trace.wallpaper-plan.v1",
    request_id: "task-1",
    time_zone: "Asia/Seoul",
    background_query: "Seoul campus morning",
    reference_ids: [],
    style: { text_color: "black" },
    rows: [{ layout: "one_by_one", components: [{ title: "오늘", events: [] }] }],
  };
  const background = {
    schema_version: "trace.background-search.v1",
    query: "Seoul campus morning",
    provider: "image-search",
    image_url: "https://images.example/background.png",
    source_url: "https://images.example/background",
    artifact_sha256: "c".repeat(64),
  };
  return {
    schema_version: "trace.hosted-generation-provenance.v1",
    plan_sha256: createHash("sha256").update(canonicalJson(plan)).digest("hex"),
    plan,
    background_sha256: createHash("sha256").update(canonicalJson(background)).digest("hex"),
    background,
  };
}

test("accepts a verified native PNG and removes base64 from the D1 receipt", async () => {
  const provenance = generationProvenance();
  const prepared = await prepareHostedCaptureResult(result({ generation_provenance: provenance }));

  assert.equal(prepared.status, "succeeded");
  assert.equal(Buffer.from(prepared.image).compare(image), 0);
  assert.equal(prepared.image_digest, digest);
  assert.equal(prepared.stored_result.output.image_base64, undefined);
  assert.equal(prepared.stored_result.output.byte_size, image.byteLength);
  assert.deepEqual(prepared.stored_result.output.generation_provenance, provenance);
});

test("rejects malformed generation provenance when a worker supplies it", async () => {
  await assert.rejects(
    prepareHostedCaptureResult(result({
      generation_provenance: { ...generationProvenance(), plan_sha256: "not-a-digest" },
    })),
    (error) => error instanceof HostedCaptureResultError && error.status === 400,
  );
});

test("rejects generation provenance whose content changed after digesting", async () => {
  const provenance = generationProvenance();
  provenance.plan.background_query = "tampered query";

  await assert.rejects(
    prepareHostedCaptureResult(result({ generation_provenance: provenance })),
    (error) => error instanceof HostedCaptureResultError && error.status === 409,
  );
});

test("binds a successful capture result to the stored candidate task", () => {
  const output = {
    pipeline: "hosted_workspace_capture_v1",
    candidate_id: "candidate-1",
    candidate_revision: 4,
    generation_provenance: generationProvenance(),
  };
  const task = { task_id: "task-1", candidate_id: "candidate-1", candidate_revision: 4 };

  assert.doesNotThrow(() => assertHostedCaptureLinkage(output, task));
  assert.throws(
    () => assertHostedCaptureLinkage({ ...output, candidate_revision: 5 }, task),
    (error) => error instanceof HostedCaptureResultError && error.status === 409,
  );
  assert.throws(
    () => assertHostedCaptureLinkage({
      ...output,
      generation_provenance: {
        ...output.generation_provenance,
        plan: { ...output.generation_provenance.plan, request_id: "different-task" },
      },
    }, task),
    (error) => error instanceof HostedCaptureResultError && error.status === 409,
  );
});

test("rejects an unverified native provenance claim", async () => {
  await assert.rejects(
    prepareHostedCaptureResult(result({ native_export_binding_verified: false })),
    (error) => error instanceof HostedCaptureResultError && error.status === 400,
  );
});

test("rejects a PNG whose callback digest changed", async () => {
  await assert.rejects(
    prepareHostedCaptureResult(result({ image_sha256: "0".repeat(64) })),
    (error) => error instanceof HostedCaptureResultError && error.status === 409,
  );
});

test("preserves a verified failure without requiring image bytes", async () => {
  const failed = { status: "failed", failure_code: "native_simulator_unavailable", output: {} };

  assert.deepEqual((await prepareHostedCaptureResult(failed)).stored_result, failed);
});
