import { createThreadsGraphClient, THREADS_REQUIRED_SCOPES, ThreadsGraphError } from "./client.js";
import { createThreadsTokenVaultFromEnv } from "./crypto.js";
import { createThreadsMediaUrl } from "./media-capability.js";

const publicationRow = (db, publicationId) => db.prepare(
  `SELECT publication.*, profile.token_ciphertext, profile.token_nonce,
          profile.token_key_version, profile.token_expires_at, profile.scopes_json,
          profile.state AS profile_state, account.threads_auto_publish_enabled
   FROM hosted_threads_publications AS publication
   JOIN hosted_threads_profiles AS profile
     ON profile.account_id = publication.account_id AND profile.profile_id = publication.profile_id
   JOIN hosted_workspace_accounts AS account ON account.account_id = publication.account_id
   WHERE publication.publication_id = ?`,
).bind(publicationId).first();

const nowIso = (now) => new Date(now()).toISOString();

const failureState = (error) => {
  if (!(error instanceof ThreadsGraphError)) return { state: "failed", code: "threads_publication_failed" };
  if (error.code === "THREADS_PUBLISH_AMBIGUOUS") {
    return { state: "unknown_side_effect", code: "publish_outcome_unknown" };
  }
  if (error.code === "THREADS_REAUTH_REQUIRED") return { state: "auth_required", code: "profile_reauth_required" };
  if (error.code === "THREADS_RATE_LIMITED") return { state: "rate_limited", code: "threads_rate_limited" };
  return { state: "failed", code: error.code.toLowerCase() };
};

const recordFailure = async (env, publicationId, fromState, error, now) => {
  const failure = failureState(error);
  const retryAfter = error instanceof ThreadsGraphError && error.details.retryAfterMs
    ? new Date(now() + error.details.retryAfterMs).toISOString()
    : null;
  await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET state = ?, failure_code = ?, failure_detail = NULL, failed_at = ?,
         retry_after_at = ?, updated_at = ?
     WHERE publication_id = ? AND state = ?`,
  ).bind(
    failure.state,
    failure.code,
    nowIso(now),
    retryAfter,
    nowIso(now),
    publicationId,
    fromState,
  ).run();
  return { publication_id: publicationId, status: failure.state, failure_code: failure.code };
};

const cancelBeforeBarrier = async (env, publicationId, state, code, now) => {
  await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET state = 'canceled', canceled_at = ?, failure_code = ?, updated_at = ?
     WHERE publication_id = ? AND state = ?`,
  ).bind(nowIso(now), code, nowIso(now), publicationId, state).run();
  return { publication_id: publicationId, status: "canceled", failure_code: code };
};

const validScopes = (row) => {
  let scopes;
  try {
    scopes = JSON.parse(row.scopes_json);
  } catch {
    return false;
  }
  return Array.isArray(scopes) && THREADS_REQUIRED_SCOPES.every((scope) => scopes.includes(scope));
};

const reconcileReadback = async (env, row, accessToken, graph, now) => {
  try {
    const post = await graph.getPost(row.threads_post_id, accessToken);
    const publishedAt = nowIso(now);
    const updated = await env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET state = 'published', permalink = ?, published_at = ?, next_poll_at = ?,
           failure_code = NULL, retry_after_at = NULL, updated_at = ?
       WHERE publication_id = ? AND state = 'publishing' AND threads_post_id = ?`,
    ).bind(
      post.permalink,
      publishedAt,
      new Date(now() + 15 * 60_000).toISOString(),
      publishedAt,
      row.publication_id,
      row.threads_post_id,
    ).run();
    if (updated.meta.changes !== 1) return { publication_id: row.publication_id, status: "stale" };
    return {
      publication_id: row.publication_id,
      status: "published",
      threads_post_id: row.threads_post_id,
      permalink: post.permalink,
    };
  } catch (error) {
    await env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET failure_code = 'readback_pending', retry_after_at = ?, updated_at = ?
       WHERE publication_id = ? AND state = 'publishing' AND threads_post_id IS NOT NULL`,
    ).bind(
      new Date(now() + 5 * 60_000).toISOString(),
      nowIso(now),
      row.publication_id,
    ).run();
    return { publication_id: row.publication_id, status: "readback_pending" };
  }
};

export async function dispatchHostedThreadsPublication(env, publicationId, options = {}) {
  const now = options.now ?? Date.now;
  const graph = options.graphClient ?? createThreadsGraphClient(env, options.graphOptions);
  const vault = options.tokenVault ?? createThreadsTokenVaultFromEnv(env);
  let row = await publicationRow(env.DB, publicationId);
  if (!row) return { publication_id: publicationId, status: "not_found" };
  const readbackOnly = row.state === "publishing" && row.threads_post_id;
  if (row.state !== "scheduled" && !readbackOnly) {
    return { publication_id: publicationId, status: row.state };
  }
  if (row.threads_auto_publish_enabled !== 1) {
    if (row.state === "scheduled") {
      return cancelBeforeBarrier(env, publicationId, "scheduled", "auto_publish_disabled", now);
    }
  }
  if (
    row.profile_state !== "active"
    || Date.parse(row.token_expires_at ?? "") <= now()
    || !validScopes(row)
    || !row.token_ciphertext
    || !row.token_nonce
    || !row.token_key_version
  ) {
    if (row.state === "scheduled") {
      return cancelBeforeBarrier(env, publicationId, "scheduled", "profile_unavailable", now);
    }
    return recordFailure(
      env,
      publicationId,
      "publishing",
      new ThreadsGraphError("THREADS_REAUTH_REQUIRED", "Threads profile authorization must be renewed"),
      now,
    );
  }
  let accessToken;
  try {
    accessToken = await vault.decrypt({
      ciphertext: row.token_ciphertext,
      nonce: row.token_nonce,
      key_version: row.token_key_version,
    });
  } catch {
    return recordFailure(
      env,
      publicationId,
      row.state,
      new ThreadsGraphError("THREADS_REAUTH_REQUIRED", "Threads profile authorization must be renewed"),
      now,
    );
  }
  if (readbackOnly) {
    return reconcileReadback(env, row, accessToken, graph, now);
  }
  const claimed = await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET state = 'creating_container', updated_at = ?
     WHERE publication_id = ? AND state = 'scheduled'
       AND EXISTS (
         SELECT 1 FROM hosted_workspace_accounts
         WHERE account_id = hosted_threads_publications.account_id
           AND enabled = 1 AND threads_auto_publish_enabled = 1
       )
       AND EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = hosted_threads_publications.account_id
           AND profile_id = hosted_threads_publications.profile_id AND state = 'active'
       )`,
  ).bind(nowIso(now), publicationId).run();
  if (claimed.meta.changes !== 1) {
    row = await publicationRow(env.DB, publicationId);
    if (row?.state === "scheduled") {
      return cancelBeforeBarrier(env, publicationId, "scheduled", "auto_publish_disabled", now);
    }
    return { publication_id: publicationId, status: row?.state ?? "not_found" };
  }
  try {
    const quota = await graph.getPublishingLimit(accessToken);
    if (quota.quotaUsage >= quota.quotaTotal) {
      return recordFailure(
        env,
        publicationId,
        "creating_container",
        new ThreadsGraphError("THREADS_RATE_LIMITED", "Threads publishing quota is exhausted"),
        now,
      );
    }
    const imageUrl = await createThreadsMediaUrl(env, row, { now });
    const container = await graph.createImageContainer({
      accessToken,
      imageUrl,
      text: row.caption_snapshot,
    });
    const containerReady = await env.DB.prepare(
      `UPDATE hosted_threads_publications
       SET state = 'container_ready', container_id = ?, container_created_at = ?, updated_at = ?
       WHERE publication_id = ? AND state = 'creating_container'`,
    ).bind(container.containerId, nowIso(now), nowIso(now), publicationId).run();
    if (containerReady.meta.changes !== 1) return { publication_id: publicationId, status: "stale" };
  } catch (error) {
    return recordFailure(env, publicationId, "creating_container", error, now);
  }
  const barrier = await env.DB.prepare(
    `UPDATE hosted_threads_publications
     SET state = 'publishing', publish_barrier_at = ?, updated_at = ?
     WHERE publication_id = ? AND state = 'container_ready'
       AND EXISTS (
         SELECT 1 FROM hosted_workspace_accounts
         WHERE account_id = hosted_threads_publications.account_id
           AND enabled = 1 AND threads_auto_publish_enabled = 1
       )
       AND EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = hosted_threads_publications.account_id
           AND profile_id = hosted_threads_publications.profile_id AND state = 'active'
       )`,
  ).bind(nowIso(now), nowIso(now), publicationId).run();
  if (barrier.meta.changes !== 1) {
    return cancelBeforeBarrier(env, publicationId, "container_ready", "auto_publish_disabled", now);
  }
  try {
    const published = await graph.publishContainer(row.container_id ?? (await publicationRow(env.DB, publicationId)).container_id, accessToken);
    const stored = await env.DB.prepare(
      `UPDATE hosted_threads_publications SET threads_post_id = ?, updated_at = ?
       WHERE publication_id = ? AND state = 'publishing' AND threads_post_id IS NULL`,
    ).bind(published.postId, nowIso(now), publicationId).run();
    if (stored.meta.changes !== 1) {
      return recordFailure(
        env,
        publicationId,
        "publishing",
        new ThreadsGraphError("THREADS_PUBLISH_AMBIGUOUS", "Threads post ID was not persisted"),
        now,
      );
    }
    row = await publicationRow(env.DB, publicationId);
    return reconcileReadback(env, row, accessToken, graph, now);
  } catch (error) {
    return recordFailure(env, publicationId, "publishing", error, now);
  }
}
