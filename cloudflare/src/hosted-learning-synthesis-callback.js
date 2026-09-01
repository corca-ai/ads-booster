import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import { MARKETING_JUDGMENT_PIPELINE } from "./marketing-agent.js";

export async function receiveHostedLearningSynthesisCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "learning synthesis callback scope is invalid");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "learning synthesis status is invalid");
  }
  const resultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== resultJson) {
      throw new HttpError(409, "learning synthesis callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  const campaign = await env.DB.prepare(
    `SELECT campaign_id, account_id, state, projection_revision
     FROM hosted_marketing_campaigns WHERE campaign_id = ? AND account_id = ?`,
  ).bind(payload.target_campaign_id, task.account_id).first();
  if (!campaign || campaign.state !== "evaluated") {
    throw new HttpError(409, "learning target campaign is stale");
  }
  const now = new Date().toISOString();
  if (status !== "succeeded") {
    if (worker) {
      const reservation = await reserveWorkerTaskCallback(
        env.DB,
        worker,
        task,
        callback.callback_id,
        resultJson,
      );
      if (reservation.duplicate) return { accepted: true, duplicate: true };
    }
    const updated = await completionStatement(
      env,
      task,
      callback,
      worker,
      status,
      resultJson,
      now,
    ).run();
    if (updated.meta.changes !== 1) throw new HttpError(409, "learning failure race");
    return { accepted: true, duplicate: false, state: "failed" };
  }
  const output = requireObject(callback.result?.output, "learning synthesis output");
  const candidate = requireObject(output.learning_candidate, "learning candidate");
  const candidateSha256 = await canonicalSha256(candidate);
  const expectedLineages = payload.lineages.map((item) => item.evaluation.evaluation_id);
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "learning_synthesis"
    || output.tool_actions_created !== 0
    || output.learning_candidate_sha256 !== candidateSha256
    || candidate.schema_version !== "trace.learning-candidate.v1"
    || candidate.learning_id !== payload.learning_id
    || candidate.campaign_id !== payload.target_campaign_id
    || candidate.status !== "candidate"
    || !sameSet(candidate.independent_lineage_ids, expectedLineages)
  ) {
    throw new HttpError(409, "learning candidate binding is invalid");
  }
  const storedLineages = await env.DB.prepare(
    `SELECT evaluation_id, campaign_id, state
     FROM hosted_marketing_experiment_evaluations
     WHERE evaluation_id IN (${expectedLineages.map(() => "?").join(",")})`,
  ).bind(...expectedLineages).all();
  if (
    storedLineages.results.length !== expectedLineages.length
    || storedLineages.results.some((item) => item.state !== "evaluated")
    || new Set(storedLineages.results.map((item) => item.campaign_id)).size
      !== expectedLineages.length
  ) {
    throw new HttpError(409, "learning replication lineage is no longer valid");
  }
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB,
      worker,
      task,
      callback.callback_id,
      resultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  const nextRevision = Number(campaign.projection_revision) + 1;
  const event = {
    campaign_id: campaign.campaign_id,
    learning_id: candidate.learning_id,
    candidate_sha256: candidateSha256,
    independent_lineage_ids: candidate.independent_lineage_ids,
  };
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_learning_candidates
        (learning_id, campaign_id, schema_version, candidate_json,
         candidate_sha256, state, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?)`,
    ).bind(
      candidate.learning_id,
      campaign.campaign_id,
      candidate.schema_version,
      canonicalJson(candidate),
      candidateSha256,
      candidate.created_at,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'learning_candidate', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'evaluated'
         AND projection_revision = ?`,
    ).bind(
      nextRevision,
      now,
      campaign.campaign_id,
      campaign.account_id,
      campaign.projection_revision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, 'learning_candidate_created', ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(event),
      await canonicalSha256(event),
      `campaign:${campaign.campaign_id}:learning:${candidateSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", resultJson, now),
  ];
  const results = await env.DB.batch(statements);
  if (results.some((result) => result?.meta?.changes !== 1)) {
    throw new HttpError(409, "learning synthesis batch lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    learning_id: candidate.learning_id,
    state: "candidate",
  };
}

function completionStatement(env, task, callback, worker, status, resultJson, now) {
  return worker
    ? env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL AND worker_id = ? AND lease_id = ?
         AND callback_reservation_id = ?`,
    ).bind(
      status,
      resultJson,
      callback.callback_id,
      now,
      task.task_id,
      worker.worker_id,
      task.lease_id,
      callback.callback_id,
    )
    : env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL`,
    ).bind(status, resultJson, callback.callback_id, now, task.task_id);
}

function publishedPayload(task) {
  try {
    return requireObject(JSON.parse(task.task_json)?.payload, "learning task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "learning task payload is invalid");
  }
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function sameSet(left, right) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === new Set(left).size
    && right.length === new Set(right).size
    && left.length === right.length
    && left.every((item) => right.includes(item));
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function canonicalSha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
