import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import {
  InvalidOutcomeReassessment,
  validateOutcomeReassessment,
} from "./marketing-outcome-reassessment.js";

const PIPELINE = "hosted_marketing_judgment_v1";

export async function receiveHostedOutcomeReassessmentCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "outcome reassessment callback scope is invalid");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "outcome reassessment status is invalid");
  }
  const resultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== resultJson) {
      throw new HttpError(409, "outcome reassessment callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  const source = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.account_id, campaign.state,
            evaluation.evaluation_id, evaluation.evaluation_json, evaluation.evaluation_sha256,
            brief.brief_id, brief.brief_json, brief.brief_sha256
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_experiment_evaluations AS evaluation
       ON evaluation.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.campaign_id = campaign.campaign_id
     WHERE campaign.campaign_id = ? AND campaign.account_id = ?
       AND evaluation.evaluation_id = ?`,
  ).bind(
    payload.campaign_id,
    task.account_id,
    payload.evaluation?.evaluation_id,
  ).first();
  if (!source || source.state !== "evaluated") {
    throw new HttpError(409, "outcome reassessment source is stale");
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
    if (updated.meta.changes !== 1) throw new HttpError(409, "reassessment failure race");
    return { accepted: true, duplicate: false, state: "failed" };
  }
  const output = requireObject(callback.result?.output, "outcome reassessment output");
  const reassessment = requireObject(output.reassessment, "outcome reassessment");
  const reassessmentSha256 = await canonicalSha256(reassessment);
  let storedEvaluation;
  let storedStrategy;
  try {
    storedEvaluation = requireObject(JSON.parse(source.evaluation_json), "stored evaluation");
    storedStrategy = requireObject(JSON.parse(source.brief_json), "stored strategy");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "outcome reassessment source records are invalid");
  }
  if (
    output.pipeline !== PIPELINE
    || output.judgment !== "outcome_reassessment"
    || output.tool_actions_created !== 0
    || output.reassessment_sha256 !== reassessmentSha256
    || payload.evaluation_sha256 !== source.evaluation_sha256
    || payload.prior_strategy_sha256 !== source.brief_sha256
    || canonicalJson(payload.evaluation) !== canonicalJson(storedEvaluation)
    || canonicalJson(payload.prior_strategy) !== canonicalJson(storedStrategy)
  ) {
    throw new HttpError(409, "outcome reassessment output binding is invalid");
  }
  try {
    validateOutcomeReassessment(payload, reassessment);
  } catch (error) {
    if (error instanceof InvalidOutcomeReassessment) {
      throw new HttpError(409, error.message);
    }
    throw error;
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
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_outcome_reassessments
        (reassessment_id, campaign_id, evaluation_id, strategy_brief_id, schema_version,
         situation, reassessment_json, reassessment_sha256, state, created_at)
       VALUES (?, ?, ?, ?, 'trace.marketing-reassessment.v1', ?, ?, ?, 'proposed', ?)`,
    ).bind(
      reassessment.reassessment_id,
      source.campaign_id,
      source.evaluation_id,
      source.brief_id,
      reassessment.situation,
      canonicalJson(reassessment),
      reassessmentSha256,
      reassessment.created_at,
    ),
    completionStatement(env, task, callback, worker, "succeeded", resultJson, now),
  ]);
  if (results.some((result) => result?.meta?.changes !== 1)) {
    throw new HttpError(409, "outcome reassessment batch lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: source.campaign_id,
    reassessment_id: reassessment.reassessment_id,
    situation: reassessment.situation,
    state: "proposed",
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
    return requireObject(JSON.parse(task.task_json)?.payload, "reassessment task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "reassessment task payload is invalid");
  }
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
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
