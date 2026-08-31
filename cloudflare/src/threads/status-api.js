const json = (payload, status = 200) => Response.json(payload, {
  status,
  headers: { "cache-control": "no-store" },
});

const accountId = (env) => env.HOSTED_WORKSPACE_ACCOUNT_ID;

const authorize = (request, env) => {
  if (!env.CONTROL_PLANE_TOKEN || request.headers.get("authorization") !== `Bearer ${env.CONTROL_PLANE_TOKEN}`) {
    const error = new Error("unauthorized");
    error.status = 401;
    throw error;
  }
};

const readJson = async (request) => {
  try {
    const value = await request.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    const error = new Error("JSON 요청 본문이 올바르지 않습니다.");
    error.status = 400;
    throw error;
  }
};

const publicationFromRow = (row) => ({
  publication_id: row.publication_id,
  candidate_id: row.candidate_id,
  candidate_revision: row.candidate_revision,
  profile: {
    profile_id: row.profile_id,
    username: row.username_snapshot,
  },
  state: row.state,
  scheduled_at: row.scheduled_at,
  timezone: row.timezone_snapshot,
  posting_slot: row.posting_slot_snapshot,
  wall_clock: row.wall_clock_snapshot ? JSON.parse(row.wall_clock_snapshot) : null,
  permalink: row.permalink,
  published_at: row.published_at,
  canceled_at: row.canceled_at,
  failure_code: row.failure_code,
  retry_after_at: row.retry_after_at,
  next_poll_at: row.next_poll_at,
  metrics_polled_at: row.metrics_polled_at,
  replies_polled_at: row.replies_polled_at,
  poll_completed_at: row.poll_completed_at,
  metrics: row.metric_observed_at ? {
    observed_at: row.metric_observed_at,
    views: row.views,
    likes: row.likes,
    replies: row.replies,
    reposts: row.reposts,
    quotes: row.quotes,
    shares: row.shares,
  } : null,
});

const listPublications = async (env) => {
  const result = await env.DB.prepare(
    `SELECT publication.*,
            metric.observed_at AS metric_observed_at, metric.views, metric.likes,
            metric.replies, metric.reposts, metric.quotes, metric.shares
     FROM hosted_threads_publications AS publication
     LEFT JOIN hosted_threads_metric_snapshots AS metric
       ON metric.snapshot_id = (
         SELECT snapshot_id FROM hosted_threads_metric_snapshots
         WHERE account_id = publication.account_id
           AND publication_id = publication.publication_id
         ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1
       )
     WHERE publication.account_id = ?
     ORDER BY publication.created_at DESC LIMIT 200`,
  ).bind(accountId(env)).all();
  return result.results.map(publicationFromRow);
};

const listReplies = async (env, publicationId) => {
  const publication = await env.DB.prepare(
    `SELECT publication_id FROM hosted_threads_publications
     WHERE account_id = ? AND publication_id = ?`,
  ).bind(accountId(env), publicationId).first();
  if (!publication) {
    const error = new Error("Threads 게시 상태를 찾을 수 없습니다.");
    error.status = 404;
    throw error;
  }
  const result = await env.DB.prepare(
    `SELECT threads_reply_id, body, replied_at, last_seen_at
     FROM hosted_threads_replies
     WHERE account_id = ? AND publication_id = ?
     ORDER BY replied_at DESC, threads_reply_id LIMIT 100`,
  ).bind(accountId(env), publicationId).all();
  return result.results.map((row) => ({
    reply_id: row.threads_reply_id,
    body: row.body,
    replied_at: row.replied_at,
    last_seen_at: row.last_seen_at,
  }));
};

const resolveUnknown = async (env, publicationId, body) => {
  const now = new Date().toISOString();
  let statement;
  if (body.decision === "reconcile") {
    if (typeof body.threads_post_id !== "string" || !/^[A-Za-z0-9_-]{1,160}$/u.test(body.threads_post_id)) {
      const error = new Error("확인할 Threads post ID가 필요합니다.");
      error.status = 400;
      throw error;
    }
    statement = env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET state = 'publishing', threads_post_id = ?, failure_code = 'readback_pending',
           retry_after_at = ?, updated_at = ?
       WHERE account_id = ? AND publication_id = ? AND state = 'unknown_side_effect'`,
    ).bind(body.threads_post_id, now, now, accountId(env), publicationId);
  } else if (body.decision === "failed") {
    statement = env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET state = 'failed', failure_code = 'manually_resolved_failed', failed_at = ?, updated_at = ?
       WHERE account_id = ? AND publication_id = ? AND state = 'unknown_side_effect'`,
    ).bind(now, now, accountId(env), publicationId);
  } else {
    const error = new Error("decision은 reconcile 또는 failed여야 합니다.");
    error.status = 400;
    throw error;
  }
  const result = await statement.run();
  if (result.meta.changes !== 1) {
    const error = new Error("게시 상태가 다른 요청에서 먼저 변경되었습니다.");
    error.status = 409;
    throw error;
  }
  return { publication_id: publicationId, status: body.decision === "reconcile" ? "publishing" : "failed" };
};

export async function handleHostedThreadsStatus(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/api/threads/publications") {
    return json({ publications: await listPublications(env) });
  }
  const route = url.pathname.match(/^\/api\/threads\/publications\/([^/]+)\/(replies|resolve)$/u);
  if (!route) return null;
  try {
    authorize(request, env);
    const publicationId = decodeURIComponent(route[1]);
    if (request.method === "GET" && route[2] === "replies") {
      return json({ replies: await listReplies(env, publicationId) });
    }
    if (request.method === "POST" && route[2] === "resolve") {
      return json(await resolveUnknown(env, publicationId, await readJson(request)));
    }
    return null;
  } catch (error) {
    return json(
      { detail: error.message },
      Number.isSafeInteger(error.status) ? error.status : 500,
    );
  }
}
