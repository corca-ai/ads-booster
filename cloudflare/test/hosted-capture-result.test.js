import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
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

test("accepts a verified native PNG and removes base64 from the D1 receipt", async () => {
  const prepared = await prepareHostedCaptureResult(result());

  assert.equal(prepared.status, "succeeded");
  assert.equal(Buffer.from(prepared.image).compare(image), 0);
  assert.equal(prepared.image_digest, digest);
  assert.equal(prepared.stored_result.output.image_base64, undefined);
  assert.equal(prepared.stored_result.output.byte_size, image.byteLength);
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
