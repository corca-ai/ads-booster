import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import { MARKETING_JUDGMENT_PIPELINE } from "./marketing-agent.js";

export async function receiveHostedCreativePlanCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
  ) {
    throw new HttpError(409, "callback scope does not match hosted creative judgment task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match hosted creative judgment task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid hosted creative judgment result status");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== storedResultJson) throw new HttpError(409, "callback result changed");
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  if (
    payload.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || payload.judgment !== "creative_plan"
  ) {
    throw new HttpError(409, "published creative judgment payload is invalid");
  }
  const campaign = await env.DB.prepare(
    `SELECT campaign_id, account_id, feature_packet_id, feature_packet_sha256, mode, state,
            projection_revision
     FROM hosted_marketing_campaigns
     WHERE campaign_id = ? AND account_id = ?
       AND EXISTS (
         SELECT 1 FROM hosted_marketing_approval_grants
         WHERE campaign_id = hosted_marketing_campaigns.campaign_id
           AND scope = 'strategy' AND target_kind = 'strategy_brief'
           AND target_id = ? AND target_sha256 = ? AND decision = 'approved'
       )`,
  ).bind(
    payload.campaign_id,
    task.account_id,
    payload.strategy_brief?.brief_id,
    payload.strategy_brief_sha256,
  ).first();
  if (!campaign || campaign.state !== "experiment_registered") {
    throw new HttpError(409, "campaign is not awaiting a creative plan");
  }
  const now = new Date().toISOString();
  if (status !== "succeeded") {
    if (worker) {
      const reservation = await reserveWorkerTaskCallback(
        env.DB,
        worker,
        task,
        callback.callback_id,
        storedResultJson,
      );
      if (reservation.duplicate) return { accepted: true, duplicate: true };
    }
    return finishFailedCreativePlan(env, task, callback, worker, campaign, storedResultJson, now);
  }

  const output = requireObject(callback.result?.output, "creative judgment output");
  const receipt = requireObject(output.context_receipt, "creative context receipt");
  const plan = requireObject(output.media_plan, "media plan");
  const receiptSha256 = await canonicalSha256(receipt);
  const planSha256 = await canonicalSha256(plan);
  const strategy = requireObject(payload.strategy_brief, "published strategy brief");
  const featurePacket = requireObject(payload.feature_packet, "published feature packet");
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "creative_plan"
    || output.campaign_id !== campaign.campaign_id
    || output.tool_actions_created !== 0
    || receiptSha256 !== output.context_receipt_sha256
    || planSha256 !== output.media_plan_sha256
  ) {
    throw new HttpError(409, "creative judgment output binding is invalid");
  }
  if (
    receipt.schema_version !== "trace.context-receipt.v1"
    || receipt.receipt_id !== task.task_id
    || receipt.campaign_id !== campaign.campaign_id
    || receipt.feature_packet_id !== campaign.feature_packet_id
    || receipt.feature_packet_sha256 !== campaign.feature_packet_sha256
    || receipt.knowledge_snapshot_sha256 !== payload.knowledge_snapshot_sha256
    || receipt.capability_snapshot_sha256 !== payload.capability_snapshot_sha256
    || plan.schema_version !== "trace.media-plan.v1"
    || plan.campaign_id !== campaign.campaign_id
    || plan.account_id !== campaign.account_id
    || plan.experiment_id !== strategy.experiment?.experiment_id
    || plan.strategy_brief_sha256 !== payload.strategy_brief_sha256
    || plan.context_receipt_sha256 !== receiptSha256
    || plan.human_review_required !== true
    || plan.publication_allowed !== featurePacket.gate?.publication_allowed
    || output.publication_allowed !== plan.publication_allowed
    || receipt.created_at !== plan.created_at
  ) {
    throw new HttpError(409, "creative media plan scope is invalid");
  }
  const treatments = validateTreatments(plan, strategy, payload.available_capabilities);
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB,
      worker,
      task,
      callback.callback_id,
      storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  const nextRevision = Number(campaign.projection_revision) + 1;
  const eventDetail = {
    campaign_id: campaign.campaign_id,
    task_id: task.task_id,
    context_receipt_id: receipt.receipt_id,
    context_receipt_sha256: receiptSha256,
    media_plan_id: plan.plan_id,
    media_plan_sha256: planSha256,
    tool_actions_created: 0,
  };
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_context_receipts
        (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
         feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
         prompt_sha256, output_schema_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      receipt.receipt_id,
      campaign.campaign_id,
      receipt.schema_version,
      canonicalJson(receipt),
      receiptSha256,
      receipt.feature_packet_sha256,
      receipt.knowledge_snapshot_sha256,
      receipt.capability_snapshot_sha256,
      receipt.prompt_sha256,
      receipt.output_schema_sha256,
      receipt.created_at,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_media_plans
        (plan_id, campaign_id, strategy_brief_id, context_receipt_id, schema_version,
         plan_json, plan_sha256, publication_allowed, human_review_required, state,
         created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'proposed', ?, ?)`,
    ).bind(
      safeId(plan.plan_id, "plan_id"),
      campaign.campaign_id,
      safeId(strategy.brief_id, "strategy_brief_id"),
      receipt.receipt_id,
      plan.schema_version,
      canonicalJson(plan),
      planSha256,
      plan.publication_allowed ? 1 : 0,
      plan.created_at,
      now,
    ),
  ];
  for (const treatment of treatments) {
    const treatmentId = safeId(treatment.treatment_id, "treatment_id");
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_creative_treatments
          (treatment_id, plan_id, campaign_id, experiment_id, hypothesis_id, format,
           treatment_json, treatment_sha256, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        treatmentId,
        plan.plan_id,
        campaign.campaign_id,
        plan.experiment_id,
        treatment.hypothesis_id,
        treatment.format,
        canonicalJson(treatment),
        await canonicalSha256(treatment),
        now,
      ),
    );
    for (const request of treatment.artifact_requests) {
      statements.push(
        env.DB.prepare(
          `INSERT INTO hosted_marketing_artifact_requests
            (request_id, campaign_id, treatment_id, capability_id, proof_kind,
             request_json, request_sha256, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)`,
        ).bind(
          safeId(request.request_id, "artifact request_id"),
          campaign.campaign_id,
          treatmentId,
          safeId(request.capability_id, "artifact capability_id"),
          request.proof_kind,
          canonicalJson(request),
          await canonicalSha256(request),
          now,
          now,
        ),
      );
    }
  }
  statements.push(
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'creative_planned', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'experiment_registered'
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
       VALUES (?, ?, ?, ?, ?, 'creative_plan_completed', ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaign.campaign_id}:creative:${planSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", storedResultJson, now),
  );
  const results = await env.DB.batch(statements);
  if (results.at(-3)?.meta?.changes !== 1 || results.at(-1)?.meta?.changes !== 1) {
    throw new HttpError(409, "creative judgment completion lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    state: "creative_planned",
    media_plan_id: plan.plan_id,
  };
}

function validateTreatments(plan, strategy, availableCapabilities) {
  const treatments = requireArray(plan.treatments, "creative treatments", 2, 8);
  const active = new Set(requireArray(
    strategy.experiment?.activated_hypothesis_ids,
    "activated strategy hypotheses",
    2,
    8,
  ));
  const hypotheses = new Map(requireArray(strategy.hypotheses, "strategy hypotheses", 2, 8)
    .map((hypothesis) => [safeId(hypothesis.hypothesis_id, "hypothesis_id"), hypothesis]));
  const capabilities = new Set(requireArray(
    availableCapabilities,
    "creative capabilities",
    1,
    32,
  ));
  const planned = new Set();
  for (const treatment of treatments) {
    const hypothesisId = safeId(treatment?.hypothesis_id, "treatment hypothesis_id");
    if (planned.has(hypothesisId) || !active.has(hypothesisId) || !hypotheses.has(hypothesisId)) {
      throw new HttpError(409, "creative plan must cover each active hypothesis exactly once");
    }
    planned.add(hypothesisId);
    const hypothesisClaims = new Set(hypotheses.get(hypothesisId).claim_ids);
    const treatmentClaims = requireArray(treatment.claim_ids, "treatment claim_ids", 1, 16);
    if (treatmentClaims.some((claimId) => !hypothesisClaims.has(claimId))) {
      throw new HttpError(409, "creative treatment escaped its strategy claims");
    }
    const requests = requireArray(treatment.artifact_requests, "artifact requests", 1, 8);
    for (const request of requests) {
      if (!capabilities.has(request?.capability_id)) {
        throw new HttpError(409, "creative treatment requested an unavailable capability");
      }
      const requestClaims = requireArray(request.claim_ids ?? [], "artifact claim_ids", 0, 16);
      if (requestClaims.some((claimId) => !treatmentClaims.includes(claimId))) {
        throw new HttpError(409, "artifact request escaped its creative treatment claims");
      }
    }
  }
  if (planned.size !== active.size) {
    throw new HttpError(409, "creative plan is missing an active hypothesis");
  }
  return treatments;
}

async function finishFailedCreativePlan(
  env,
  task,
  callback,
  worker,
  campaign,
  storedResultJson,
  now,
) {
  const nextRevision = Number(campaign.projection_revision) + 1;
  const detail = {
    campaign_id: campaign.campaign_id,
    task_id: task.task_id,
    status: callback.result.status,
    failure_code: callback.result.failure_code,
  };
  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'failed', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'experiment_registered'
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
       VALUES (?, ?, ?, ?, ?, 'creative_plan_failed', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(detail),
      await canonicalSha256(detail),
      `campaign:${campaign.campaign_id}:creative-failed:${task.task_id}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, callback.result.status, storedResultJson, now),
  ]);
  if (results[0]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
    throw new HttpError(409, "creative judgment failure lost its state race");
  }
  return { accepted: true, duplicate: false, campaign_id: campaign.campaign_id, state: "failed" };
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
    return requireObject(JSON.parse(task.task_json)?.payload, "published creative payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "published creative payload is invalid");
  }
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function requireArray(value, name, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function safeId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
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
