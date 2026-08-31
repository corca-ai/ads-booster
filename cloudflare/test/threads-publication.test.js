import assert from "node:assert/strict";
import test from "node:test";

import { ThreadsGraphError } from "../src/threads/client.js";
import {
  createThreadsMediaUrl,
  handleThreadsMediaRequest,
} from "../src/threads/media-capability.js";
import { dispatchHostedThreadsPublication } from "../src/threads/publication.js";
import { createPublicationFixture } from "./threads-publication-fixture.js";

const NOW = Date.parse("2026-08-31T00:00:00Z");

const dispatch = (fixture) => dispatchHostedThreadsPublication(
  fixture.env,
  fixture.row.publication_id,
  {
    graphClient: fixture.graphClient,
    tokenVault: fixture.tokenVault,
    now: () => NOW,
  },
);

test("claims, creates one container, crosses the barrier, publishes once, and reads back", async () => {
  const fixture = createPublicationFixture();
  const result = await dispatch(fixture);
  assert.equal(result.status, "published");
  assert.equal(fixture.row.state, "published");
  assert.equal(fixture.row.threads_post_id, "post-a");
  assert.deepEqual(fixture.calls, ["decrypt", "quota", "container", "publish", "readback"]);
  const duplicate = await dispatch(fixture);
  assert.equal(duplicate.status, "published");
  assert.equal(fixture.calls.filter((call) => call === "publish").length, 1);
});

test("terminal publication does not require a retained profile token", async () => {
  const fixture = createPublicationFixture({
    row: {
      state: "published",
      token_ciphertext: null,
      token_nonce: null,
      token_key_version: null,
    },
  });
  assert.equal((await dispatch(fixture)).status, "published");
  assert.deepEqual(fixture.calls, []);
});

test("OFF before claim and OFF after container both cancel before publish", async () => {
  const before = createPublicationFixture({ row: { threads_auto_publish_enabled: 0 } });
  assert.equal((await dispatch(before)).failure_code, "auto_publish_disabled");
  assert.equal(before.calls.includes("publish"), false);

  const race = createPublicationFixture({ disableAtContainer: true });
  const result = await dispatch(race);
  assert.equal(result.status, "canceled");
  assert.equal(race.row.state, "canceled");
  assert.deepEqual(race.calls, ["decrypt", "quota", "container"]);
});

test("ambiguous publish is terminal and never calls publish again", async () => {
  const fixture = createPublicationFixture({
    publishError: new ThreadsGraphError(
      "THREADS_PUBLISH_AMBIGUOUS",
      "publish outcome unknown",
    ),
  });
  const first = await dispatch(fixture);
  assert.equal(first.status, "unknown_side_effect");
  assert.equal(fixture.row.state, "unknown_side_effect");
  const second = await dispatch(fixture);
  assert.equal(second.status, "unknown_side_effect");
  assert.equal(fixture.calls.filter((call) => call === "publish").length, 1);
});

test("failed readback reconciles by post ID without a second publish", async () => {
  const fixture = createPublicationFixture({ readbackFailures: 1 });
  const first = await dispatch(fixture);
  assert.equal(first.status, "readback_pending");
  assert.equal(fixture.row.state, "publishing");
  assert.equal(fixture.row.threads_post_id, "post-a");
  const second = await dispatch(fixture);
  assert.equal(second.status, "published");
  assert.equal(fixture.calls.filter((call) => call === "publish").length, 1);
  assert.equal(fixture.calls.filter((call) => call === "readback").length, 2);
});

test("signed media is account, publication, digest, and expiry bound", async () => {
  const bytes = new TextEncoder().encode("png fixture bytes");
  const digestBuffer = await crypto.subtle.digest("SHA-256", bytes);
  const digest = [...new Uint8Array(digestBuffer)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  const fixture = createPublicationFixture({ imageDigest: digest });
  fixture.env.ARTIFACTS = {
    async get(key) {
      assert.equal(key, fixture.row.image_key_snapshot);
      return { async arrayBuffer() { return bytes.buffer; } };
    },
  };
  const url = await createThreadsMediaUrl(fixture.env, fixture.row, { now: () => NOW });
  const valid = await handleThreadsMediaRequest(new Request(url), fixture.env, { now: () => NOW });
  assert.equal(valid.status, 200);
  assert.equal(valid.headers.get("content-type"), "image/png");

  const tampered = new URL(url);
  tampered.searchParams.set("account_id", "account-b");
  assert.equal(
    (await handleThreadsMediaRequest(new Request(tampered), fixture.env, { now: () => NOW })).status,
    403,
  );
  assert.equal(
    (await handleThreadsMediaRequest(
      new Request(url),
      fixture.env,
      { now: () => NOW + 11 * 60_000 },
    )).status,
    403,
  );
});
