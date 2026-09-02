import assert from "node:assert/strict";
import test from "node:test";

import {
  ReferenceVerificationError,
  verifyReferenceSources,
} from "../src/reference-source-verification.js";

const SNAPSHOT_SHA = "a".repeat(64);

function snapshot(urls = ["https://example.com/one", "https://example.org/two"]) {
  return {
    snapshot_id: "snapshot-verification-1",
    sources: urls.map((url, index) => ({ source_id: `source-${index + 1}`, url })),
  };
}

function textResponse(body, options = {}) {
  return new Response(body, {
    status: options.status ?? 200,
    headers: {
      "content-type": options.contentType ?? "text/html; charset=utf-8",
      ...(options.headers ?? {}),
    },
  });
}

test("reference verification binds independently fetched bytes for every source", async () => {
  const requested = [];
  const bundle = await verifyReferenceSources(
    snapshot(),
    SNAPSHOT_SHA,
    async (url, init) => {
      requested.push({ url, redirect: init.redirect });
      return textResponse(`frozen source body: ${url}`);
    },
    "2026-09-02T00:00:00.000Z",
  );

  assert.equal(bundle.schema_version, "trace.reference-verification.v1");
  assert.equal(bundle.receipts.length, 2);
  assert.equal(new Set(bundle.receipts.map((item) => item.content_sha256)).size, 2);
  assert.deepEqual(requested.map((item) => item.redirect), ["manual", "manual"]);
  assert.deepEqual(
    bundle.receipts.map((item) => item.requested_url),
    ["https://example.com/one", "https://example.org/two"],
  );
});

test("reference verification requires independent requested and final hosts", async () => {
  await assert.rejects(
    verifyReferenceSources(
      snapshot(["https://example.com/one", "https://www.example.com/two"]),
      SNAPSHOT_SHA,
      async (url) => textResponse(url),
    ),
    (error) => error instanceof ReferenceVerificationError
      && error.message === "reference research needs two independent hosts",
  );
});

test("reference verification rejects private redirect targets before fetching them", async () => {
  const requested = [];
  await assert.rejects(
    verifyReferenceSources(snapshot(), SNAPSHOT_SHA, async (url) => {
      requested.push(url);
      if (url === "https://example.com/one") {
        return textResponse("", { status: 302, headers: { location: "https://[::1]/secret" } });
      }
      return textResponse(url);
    }),
    (error) => error instanceof ReferenceVerificationError
      && error.message === "reference source URL is not public HTTPS",
  );
  assert.deepEqual(requested, ["https://example.com/one"]);
});

test("reference verification rejects oversized or unsupported source bodies", async () => {
  await assert.rejects(
    verifyReferenceSources(snapshot(), SNAPSHOT_SHA, async () => textResponse("x", {
      headers: { "content-length": String(1024 * 1024 + 1) },
    })),
    (error) => error instanceof ReferenceVerificationError
      && error.message === "reference source is too large",
  );
  await assert.rejects(
    verifyReferenceSources(
      snapshot(),
      SNAPSHOT_SHA,
      async () => textResponse("x".repeat(1024 * 1024 + 1)),
    ),
    (error) => error instanceof ReferenceVerificationError
      && error.message === "reference source body size is invalid",
  );
  await assert.rejects(
    verifyReferenceSources(snapshot(), SNAPSHOT_SHA, async () => textResponse("image", {
      contentType: "image/png",
    })),
    (error) => error instanceof ReferenceVerificationError
      && error.message === "reference source content type is unsupported",
  );
});
