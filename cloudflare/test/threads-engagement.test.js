import assert from "node:assert/strict";
import test from "node:test";

import { ThreadsGraphError } from "../src/threads/client.js";
import {
  nextThreadsEngagementPollAt,
  runHostedThreadsEngagement,
} from "../src/threads/engagement.js";

const NOW = "2026-08-31T00:15:00.000Z";

function engagementFixture(graphClient) {
  const row = {
    publication_id: "publication-a",
    account_id: "account-a",
    profile_id: "profile-a",
    state: "published",
    threads_post_id: "post-a",
    published_at: "2026-08-31T00:00:00.000Z",
    next_poll_at: NOW,
    replies_cursor: null,
    token_ciphertext: "ciphertext",
    token_nonce: "nonce",
    token_key_version: "v1",
    token_expires_at: "2027-08-31T00:00:00Z",
  };
  const snapshots = [];
  const replies = new Map();
  let profileState = "active";
  let cleanupRuns = 0;

  const apply = async (statement) => {
    const { sql, values } = statement;
    if (sql.includes("SET next_poll_at = ?, updated_at = ?") && sql.includes("next_poll_at <= ?")) {
      const changes = row.state === "published" && row.next_poll_at <= values[3] ? 1 : 0;
      if (changes) row.next_poll_at = values[0];
      return { meta: { changes } };
    }
    if (sql.includes("INSERT INTO hosted_threads_metric_snapshots")) {
      snapshots.push({ observed_at: values[3], views: values[4], likes: values[5] });
      return { meta: { changes: 1 } };
    }
    if (sql.includes("INSERT INTO hosted_threads_replies")) {
      replies.set(values[3], { body: values[5], last_seen_at: values[8] });
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET metrics_polled_at")) {
      row.metrics_polled_at = values[0];
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET replies_cursor")) {
      row.replies_cursor = values[0];
      row.replies_polled_at = values[1];
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET next_poll_at = ?, poll_completed_at")) {
      row.next_poll_at = values[0];
      row.poll_completed_at = values[1];
      row.failure_code = values[2];
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET state = 'reauth_required'")) {
      profileState = "reauth_required";
      return { meta: { changes: 1 } };
    }
    if (sql.includes("profile_reauth_required")) {
      row.failure_code = "profile_reauth_required";
      row.next_poll_at = null;
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET state = 'unavailable'")) {
      row.state = "unavailable";
      row.failure_code = "threads_post_deleted";
      row.next_poll_at = null;
      return { meta: { changes: 1 } };
    }
    if (sql.includes("SET failure_code = ?, retry_after_at")) {
      row.failure_code = values[0];
      row.retry_after_at = values[1];
      row.next_poll_at = values[2];
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("DELETE FROM hosted_threads_")) {
      cleanupRuns += 1;
      return { meta: { changes: 0 } };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  };

  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          const statement = { sql, values };
          return {
            ...statement,
            async all() {
              assert.match(sql, /LIMIT 20/u);
              assert.doesNotMatch(sql, /threads_auto_publish_enabled/u);
              return { results: row.state === "published" && profileState === "active" ? [{ ...row }] : [] };
            },
            run() { return apply(statement); },
          };
        },
      };
    },
    async batch(statements) {
      return Promise.all(statements.map(apply));
    },
  };

  return {
    row,
    snapshots,
    replies,
    profileState: () => profileState,
    cleanupRuns: () => cleanupRuns,
    env: { DB },
    options: {
      graphClient,
      tokenVault: { async decrypt() { return "access-placeholder"; } },
      now: () => Date.parse(NOW),
    },
  };
}

test("poll cadence follows 15m, 1h, 6h, 24h, then daily through day 30", () => {
  assert.equal(nextThreadsEngagementPollAt("2026-08-31T00:00:00Z", NOW).toISOString(), "2026-08-31T01:00:00.000Z");
  assert.equal(nextThreadsEngagementPollAt("2026-08-31T00:00:00Z", "2026-08-31T06:00:00Z").toISOString(), "2026-09-01T00:00:00.000Z");
  assert.equal(nextThreadsEngagementPollAt("2026-08-31T00:00:00Z", "2026-09-02T00:00:00Z").toISOString(), "2026-09-03T00:00:00.000Z");
  assert.equal(nextThreadsEngagementPollAt("2026-08-31T00:00:00Z", "2026-09-30T00:00:00Z"), null);
});

test("stores non-monotonic snapshots and deduplicates updated top-level replies while OFF is irrelevant", async () => {
  const metricValues = [10, 8];
  const replyBodies = ["first", "updated"];
  const fixture = engagementFixture({
    async getPostInsights() {
      const views = metricValues.shift();
      return { views, likes: 2, replies: 1, reposts: 0, quotes: 0, shares: 0 };
    },
    async listTopLevelReplies(_postId, _token, options) {
      assert.equal(options.maxPages, 5);
      return {
        replies: [{ id: "reply-a", text: replyBodies.shift(), timestamp: NOW }],
        nextCursor: null,
      };
    },
  });
  assert.equal((await runHostedThreadsEngagement(fixture.env, fixture.options))[0].status, "succeeded");
  fixture.row.next_poll_at = NOW;
  assert.equal((await runHostedThreadsEngagement(fixture.env, fixture.options))[0].status, "succeeded");
  assert.deepEqual(fixture.snapshots.map((item) => item.views), [10, 8]);
  assert.equal(fixture.replies.size, 1);
  assert.equal(fixture.replies.get("reply-a").body, "updated");
  assert.equal(fixture.cleanupRuns(), 4);
});

test("metrics success remains visible when reply polling fails", async () => {
  const fixture = engagementFixture({
    async getPostInsights() {
      return { views: 3, likes: 1, replies: 0, reposts: 0, quotes: 0, shares: 0 };
    },
    async listTopLevelReplies() { throw new Error("reply fixture failure"); },
  });
  const outcome = (await runHostedThreadsEngagement(fixture.env, fixture.options))[0];
  assert.equal(outcome.status, "partial_failure");
  assert.equal(outcome.metrics, "succeeded");
  assert.equal(outcome.replies, "failed");
  assert.equal(fixture.snapshots.length, 1);
  assert.equal(fixture.row.failure_code, "replies_poll_failed");
});

test("reauth, rate limit, and deleted post pause without corrupting prior data", async () => {
  for (const [error, expected] of [
    [new ThreadsGraphError("THREADS_REAUTH_REQUIRED", "reauth"), "reauth_required"],
    [new ThreadsGraphError("THREADS_RATE_LIMITED", "rate", { details: { retryAfterMs: 1000 } }), "rate_limited"],
    [new ThreadsGraphError("THREADS_RESOURCE_NOT_FOUND", "deleted"), "unavailable"],
  ]) {
    const fixture = engagementFixture({
      async getPostInsights() { throw error; },
      async listTopLevelReplies() { throw new Error("must not run"); },
    });
    const outcome = (await runHostedThreadsEngagement(fixture.env, fixture.options))[0];
    assert.equal(outcome.status, expected);
    assert.equal(fixture.snapshots.length, 0);
    assert.equal(fixture.replies.size, 0);
  }
});
