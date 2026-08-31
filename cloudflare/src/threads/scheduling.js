export async function runHostedThreadsPublications(env, dispatch, options = {}) {
  const now = new Date((options.now ?? Date.now)()).toISOString();
  const result = await env.DB.prepare(
    `SELECT publication_id FROM hosted_threads_publications
     WHERE (state = 'scheduled' AND scheduled_at <= ?)
        OR (state = 'publishing' AND threads_post_id IS NOT NULL
            AND retry_after_at IS NOT NULL AND retry_after_at <= ?)
     ORDER BY scheduled_at, created_at LIMIT 20`,
  ).bind(now, now).all();
  const outcomes = [];
  for (const row of result.results) {
    try {
      await dispatch(env, row.publication_id);
      outcomes.push({ publication_id: row.publication_id, status: "succeeded" });
    } catch (error) {
      outcomes.push({
        publication_id: row.publication_id,
        status: "failed",
        error_code: typeof error?.code === "string" ? error.code : "threads_publication_failed",
      });
    }
  }
  return outcomes;
}
