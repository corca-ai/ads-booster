import assert from "node:assert/strict";
import test from "node:test";

import { handleHostedThreadsStatus } from "../src/threads/status-api.js";

function environment() {
  const publication = {
    publication_id: "publication-a",
    candidate_id: "candidate-a",
    candidate_revision: 2,
    profile_id: "profile-a",
    username_snapshot: "trace_a",
    state: "unknown_side_effect",
    scheduled_at: "2026-08-31T10:30:00Z",
    timezone_snapshot: "Asia/Seoul",
    posting_slot_snapshot: "evening",
    wall_clock_snapshot: '{"timezone":"Asia/Seoul","time":"19:30"}',
    permalink: null,
    published_at: null,
    canceled_at: null,
    failure_code: "publish_outcome_unknown",
    retry_after_at: null,
    next_poll_at: null,
    metrics_polled_at: null,
    replies_polled_at: null,
    poll_completed_at: null,
    metric_observed_at: "2026-08-31T11:00:00Z",
    views: 10,
    likes: 2,
    replies: 1,
    reposts: 0,
    quotes: 0,
    shares: 1,
  };
  const reply = {
    threads_reply_id: "reply-a",
    body: "top-level reply",
    replied_at: "2026-08-31T11:00:00Z",
    last_seen_at: "2026-08-31T11:05:00Z",
  };
  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async all() {
              if (sql.includes("FROM hosted_threads_publications AS publication")) {
                return { results: [{ ...publication }] };
              }
              if (sql.includes("FROM hosted_threads_replies")) return { results: [{ ...reply }] };
              throw new Error(`unexpected all SQL: ${sql}`);
            },
            async first() {
              if (sql.includes("SELECT publication_id FROM hosted_threads_publications")) {
                return values[0] === "account-a" && values[1] === publication.publication_id
                  ? { publication_id: publication.publication_id }
                  : null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
            async run() {
              if (sql.includes("state = 'publishing'")) {
                if (publication.state !== "unknown_side_effect") return { meta: { changes: 0 } };
                publication.state = "publishing";
                publication.threads_post_id = values[0];
                return { meta: { changes: 1 } };
              }
              if (sql.includes("state = 'failed'")) {
                if (publication.state !== "unknown_side_effect") return { meta: { changes: 0 } };
                publication.state = "failed";
                return { meta: { changes: 1 } };
              }
              throw new Error(`unexpected run SQL: ${sql}`);
            },
          };
        },
      };
    },
  };
  return {
    publication,
    env: {
      DB,
      HOSTED_WORKSPACE_ACCOUNT_ID: "account-a",
      CONTROL_PLANE_TOKEN: "control-placeholder",
    },
  };
}

const authorized = (url, init = {}) => new Request(url, {
  ...init,
  headers: {
    authorization: "Bearer control-placeholder",
    ...(init.body ? { "content-type": "application/json" } : {}),
  },
});

test("public publication state exposes safe metrics without reply bodies", async () => {
  const fixture = environment();
  const response = await handleHostedThreadsStatus(
    new Request("https://workspace.example/api/threads/publications"),
    fixture.env,
  );
  const body = await response.json();
  assert.equal(body.publications[0].state, "unknown_side_effect");
  assert.equal(body.publications[0].metrics.views, 10);
  assert.equal(JSON.stringify(body).includes("top-level reply"), false);
  assert.equal(JSON.stringify(body).includes("token"), false);
});

test("reply content is privileged and account-scoped", async () => {
  const fixture = environment();
  const path = "https://workspace.example/api/threads/publications/publication-a/replies";
  assert.equal((await handleHostedThreadsStatus(new Request(path), fixture.env)).status, 401);
  const response = await handleHostedThreadsStatus(authorized(path), fixture.env);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).replies, [{
    reply_id: "reply-a",
    body: "top-level reply",
    replied_at: "2026-08-31T11:00:00Z",
    last_seen_at: "2026-08-31T11:05:00Z",
  }]);
});

test("unknown resolution accepts authoritative ID for readback and never exposes publish retry", async () => {
  const fixture = environment();
  const path = "https://workspace.example/api/threads/publications/publication-a/resolve";
  const response = await handleHostedThreadsStatus(authorized(path, {
    method: "POST",
    body: JSON.stringify({ decision: "reconcile", threads_post_id: "post-a" }),
  }), fixture.env);
  assert.equal(response.status, 200);
  assert.equal(fixture.publication.state, "publishing");
  assert.equal(fixture.publication.threads_post_id, "post-a");
  assert.doesNotMatch(await response.text(), /retry.?publish/iu);
});
