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
      artifact_role: "trace_wallpaper",
      image_postprocess_source: "none",
      native_image_sha256: digest,
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

test("rejects a native result with a non-Trace artifact role", async () => {
  await assert.rejects(
    prepareHostedCaptureResult(result({ artifact_role: "imagen_ios_ui" })),
    (error) => error instanceof HostedCaptureResultError && error.status === 400,
  );
});

test("rejects a PNG whose callback digest changed", async () => {
  await assert.rejects(
    prepareHostedCaptureResult(result({
      image_sha256: "0".repeat(64),
      native_image_sha256: "0".repeat(64),
    })),
    (error) => error instanceof HostedCaptureResultError && error.status === 409,
  );
});

test("preserves a verified failure without requiring image bytes", async () => {
  const failed = { status: "failed", failure_code: "native_simulator_unavailable", output: {} };

  assert.deepEqual((await prepareHostedCaptureResult(failed)).stored_result, failed);
});

test("accepts an ImageGen iOS UI image only with verified Trace source provenance", async () => {
  const derived = result({
    capture_source: "imagen_ios_ui",
    artifact_role: "imagen_ios_ui",
    native_image_sha256: "a".repeat(64),
    source_trace_artifact_sha256: "a".repeat(64),
    image_postprocess_source: "imagen_ios_ui",
    imagegen_ui_layer_verified: true,
  });

  const prepared = await prepareHostedCaptureResult(derived);

  assert.equal(prepared.status, "succeeded");
  assert.equal(prepared.stored_result.output.artifact_role, "imagen_ios_ui");
});

test("rejects an ImageGen iOS UI image without its Trace source digest", async () => {
  const derived = result({
    capture_source: "imagen_ios_ui",
    artifact_role: "imagen_ios_ui",
    image_postprocess_source: "imagen_ios_ui",
    imagegen_ui_layer_verified: true,
  });
  delete derived.output.source_trace_artifact_sha256;

  await assert.rejects(
    prepareHostedCaptureResult(derived),
    (error) => error instanceof HostedCaptureResultError && error.status === 400,
  );
});
