import { createThreadsGraphClient, ThreadsGraphError } from "./client.js";
import { createThreadsTokenVaultFromEnv } from "./crypto.js";

const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;
const POLL_MILESTONES = [15 * MINUTE_MS, 60 * MINUTE_MS, 6 * 60 * MINUTE_MS, DAY_MS];

export function nextThreadsEngagementPollAt(publishedAt, after) {
  const published = new Date(publishedAt).getTime();
  const current = new Date(after).getTime();
  if (!Number.isFinite(published) || !Number.isFinite(current) || current < published) return null;
  for (const offset of POLL_MILESTONES) {
    const candidate = published + offset;
    if (candidate > current) return new Date(candidate);
  }
  const nextDay = Math.floor((current - published) / DAY_MS) + 1;
  if (nextDay > 30) return null;
  return new Date(published + nextDay * DAY_MS);
}

const duePublications = (db, now) => db.prepare(
  `SELECT publication.*, profile.token_ciphertext, profile.token_nonce,
          profile.token_key_version, profile.token_expires_at
   FROM hosted_threads_publications AS publication
   JOIN hosted_threads_profiles AS profile
     ON profile.account_id = publication.account_id AND profile.profile_id = publication.profile_id
   WHERE publication.state = 'published' AND publication.next_poll_at <= ?
     AND profile.state = 'active'
   ORDER BY publication.next_poll_at, publication.updated_at LIMIT 20`,
).bind(now).all();

const claimPublication = (db, publicationId, now, leaseUntil) => db.prepare(
  `UPDATE hosted_threads_publications SET next_poll_at = ?, updated_at = ?
   WHERE publication_id = ? AND state = 'published' AND next_poll_at <= ?`,
).bind(leaseUntil, now, publicationId, now).run();

const metricStatement = (env, row, metrics, observedAt) => env.DB.prepare(
  `INSERT INTO hosted_threads_metric_snapshots
    (snapshot_id, account_id, publication_id, observed_at, views, likes, replies,
     reposts, quotes, shares, delete_after)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
).bind(
  crypto.randomUUID(),
  row.account_id,
  row.publication_id,
  observedAt,
  metrics.views,
  metrics.likes,
  metrics.replies,
  metrics.reposts,
  metrics.quotes,
  metrics.shares,
  new Date(new Date(observedAt).getTime() + 365 * DAY_MS).toISOString(),
);

const replyStatements = (env, row, replies, observedAt) => replies.map((reply) => env.DB.prepare(
  `INSERT INTO hosted_threads_replies
    (reply_id, account_id, publication_id, threads_reply_id, root_threads_post_id,
     body, replied_at, first_seen_at, last_seen_at, delete_after)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   ON CONFLICT(account_id, threads_reply_id) DO UPDATE SET
     body = excluded.body, last_seen_at = excluded.last_seen_at,
     delete_after = excluded.delete_after`,
).bind(
  crypto.randomUUID(),
  row.account_id,
  row.publication_id,
  reply.id,
  row.threads_post_id,
  reply.text,
  reply.timestamp,
  observedAt,
  observedAt,
  new Date(new Date(observedAt).getTime() + 30 * DAY_MS).toISOString(),
));

const markProfileReauth = async (env, row, now) => {
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE hosted_threads_profiles SET state = 'reauth_required', updated_at = ?
       WHERE account_id = ? AND profile_id = ? AND state = 'active'`,
    ).bind(now, row.account_id, row.profile_id),
    env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET failure_code = 'profile_reauth_required', next_poll_at = NULL, updated_at = ?
       WHERE publication_id = ? AND state = 'published'`,
    ).bind(now, row.publication_id),
  ]);
};

const markPollError = async (env, row, error, now) => {
  if (error instanceof ThreadsGraphError && error.code === "THREADS_REAUTH_REQUIRED") {
    await markProfileReauth(env, row, now);
    return "reauth_required";
  }
  if (error instanceof ThreadsGraphError && error.code === "THREADS_RESOURCE_NOT_FOUND") {
    await env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET state = 'unavailable', failure_code = 'threads_post_deleted',
           next_poll_at = NULL, updated_at = ? WHERE publication_id = ? AND state = 'published'`,
    ).bind(now, row.publication_id).run();
    return "unavailable";
  }
  const retryMs = error instanceof ThreadsGraphError && error.code === "THREADS_RATE_LIMITED"
    ? error.details.retryAfterMs ?? 15 * MINUTE_MS
    : 15 * MINUTE_MS;
  const code = error instanceof ThreadsGraphError ? error.code.toLowerCase() : "threads_poll_failed";
  await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET failure_code = ?, retry_after_at = ?, next_poll_at = ?, updated_at = ?
     WHERE publication_id = ? AND state = 'published'`,
  ).bind(
    code,
    new Date(new Date(now).getTime() + retryMs).toISOString(),
    new Date(new Date(now).getTime() + retryMs).toISOString(),
    now,
    row.publication_id,
  ).run();
  return error instanceof ThreadsGraphError && error.code === "THREADS_RATE_LIMITED"
    ? "rate_limited"
    : "partial_failure";
};

const pollPublication = async (env, row, graph, vault, now) => {
  const accessToken = await vault.decrypt({
    ciphertext: row.token_ciphertext,
    nonce: row.token_nonce,
    key_version: row.token_key_version,
  });
  let metrics = "failed";
  let replies = "failed";
  try {
    const values = await graph.getPostInsights(row.threads_post_id, accessToken);
    await env.DB.batch([
      metricStatement(env, row, values, now),
      env.DB.prepare(
        `UPDATE hosted_threads_publications
         SET metrics_polled_at = ?, updated_at = ?
         WHERE publication_id = ? AND state = 'published'`,
      ).bind(now, now, row.publication_id),
    ]);
    metrics = "succeeded";
  } catch (error) {
    const state = await markPollError(env, row, error, now);
    if (["reauth_required", "unavailable", "rate_limited"].includes(state)) {
      return { publication_id: row.publication_id, status: state, metrics, replies };
    }
  }
  try {
    const page = await graph.listTopLevelReplies(row.threads_post_id, accessToken, {
      cursor: row.replies_cursor,
      maxPages: 5,
    });
    await env.DB.batch([
      ...replyStatements(env, row, page.replies, now),
      env.DB.prepare(
        `UPDATE hosted_threads_publications
         SET replies_cursor = ?, replies_polled_at = ?, updated_at = ?
         WHERE publication_id = ? AND state = 'published'`,
      ).bind(page.nextCursor, now, now, row.publication_id),
    ]);
    replies = "succeeded";
  } catch (error) {
    const state = await markPollError(env, row, error, now);
    if (["reauth_required", "unavailable", "rate_limited"].includes(state)) {
      return { publication_id: row.publication_id, status: state, metrics, replies };
    }
  }
  const succeeded = metrics === "succeeded" && replies === "succeeded";
  const next = succeeded
    ? nextThreadsEngagementPollAt(row.published_at, now)
    : new Date(new Date(now).getTime() + 15 * MINUTE_MS);
  const failureCode = succeeded
    ? null
    : metrics === "failed" && replies === "failed"
      ? "metrics_and_replies_poll_failed"
      : metrics === "failed"
        ? "metrics_poll_failed"
        : "replies_poll_failed";
  await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET next_poll_at = ?, poll_completed_at = ?, failure_code = ?,
         retry_after_at = NULL, updated_at = ?
     WHERE publication_id = ? AND state = 'published'`,
  ).bind(
    next?.toISOString() ?? null,
    next ? null : now,
    failureCode,
    now,
    row.publication_id,
  ).run();
  return {
    publication_id: row.publication_id,
    status: succeeded ? "succeeded" : "partial_failure",
    metrics,
    replies,
  };
};

const cleanup = (env, now) => env.DB.batch([
  env.DB.prepare(
    `DELETE FROM hosted_threads_replies WHERE reply_id IN
     (SELECT reply_id FROM hosted_threads_replies WHERE delete_after <= ? LIMIT 200)`,
  ).bind(now),
  env.DB.prepare(
    `DELETE FROM hosted_threads_metric_snapshots WHERE snapshot_id IN
     (SELECT snapshot_id FROM hosted_threads_metric_snapshots WHERE delete_after <= ? LIMIT 200)`,
  ).bind(now),
]);

export async function runHostedThreadsEngagement(env, options = {}) {
  const now = new Date((options.now ?? Date.now)()).toISOString();
  const due = await duePublications(env.DB, now);
  const graph = options.graphClient ?? createThreadsGraphClient(env, options.graphOptions);
  const vault = options.tokenVault ?? createThreadsTokenVaultFromEnv(env);
  const outcomes = [];
  for (const row of due.results) {
    const claimed = await claimPublication(
      env.DB,
      row.publication_id,
      now,
      new Date(new Date(now).getTime() + 5 * MINUTE_MS).toISOString(),
    );
    if (claimed.meta.changes !== 1) continue;
    try {
      outcomes.push(await pollPublication(env, row, graph, vault, now));
    } catch {
      outcomes.push({ publication_id: row.publication_id, status: "failed" });
    }
  }
  await cleanup(env, now);
  return outcomes;
}
