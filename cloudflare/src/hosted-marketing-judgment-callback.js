import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import { MARKETING_JUDGMENT_PIPELINE } from "./marketing-agent.js";

export async function receiveHostedMarketingJudgmentCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
  ) {
    throw new HttpError(409, "callback scope does not match hosted marketing judgment task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match hosted marketing judgment task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid hosted marketing judgment result status");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== storedResultJson) throw new HttpError(409, "callback result changed");
    return { accepted: true, duplicate: true };
  }
  const publishedPayload = publishedJudgmentPayload(task);
  const campaign = await env.DB.prepare(
    `SELECT campaign_id, account_id, feature_packet_id, feature_packet_sha256, mode, state,
            projection_revision, business_outcome, marketing_context_snapshot_id,
            marketing_context_snapshot_sha256
     FROM hosted_marketing_campaigns WHERE campaign_id = ? AND account_id = ?`,
  ).bind(task.run_id, task.account_id).first();
  if (
    !campaign
    || !["shadow", "assisted"].includes(campaign.mode)
    || campaign.state !== "strategy_requested"
  ) {
    throw new HttpError(409, "agent campaign is not awaiting a strategy judgment");
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
    return finishFailedJudgment(env, task, callback, worker, campaign, storedResultJson, now);
  }

  const output = requireObject(callback.result?.output, "marketing judgment output");
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "shadow_strategy"
    || output.campaign_id !== campaign.campaign_id
    || output.publication_allowed !== (publishedPayload.feature_packet?.gate?.publication_allowed === true)
    || publishedPayload.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || publishedPayload.judgment !== "shadow_strategy"
    || publishedPayload.campaign_id !== campaign.campaign_id
    || (publishedPayload.mode ?? "shadow") !== campaign.mode
  ) {
    throw new HttpError(409, "marketing judgment output binding is invalid");
  }
  const receipt = requireObject(output.context_receipt, "context receipt");
  const brief = requireObject(output.strategy_brief, "strategy brief");
  const receiptSha256 = await canonicalSha256(receipt);
  const briefSha256 = await canonicalSha256(brief);
  const marketingContext = await currentMarketingContextProjection(env.DB, campaign, now);
  if (
    receiptSha256 !== output.context_receipt_sha256
    || briefSha256 !== output.strategy_brief_sha256
  ) {
    throw new HttpError(409, "marketing judgment receipt digest is invalid");
  }
  if (
    receipt.schema_version !== "trace.context-receipt.v1"
    || receipt.receipt_id !== task.task_id
    || receipt.campaign_id !== campaign.campaign_id
    || receipt.feature_packet_id !== campaign.feature_packet_id
    || receipt.feature_packet_sha256 !== campaign.feature_packet_sha256
    || brief.schema_version !== "trace.strategy-brief.v1"
    || brief.campaign_id !== campaign.campaign_id
    || brief.account_id !== campaign.account_id
    || brief.feature_packet_id !== campaign.feature_packet_id
    || brief.feature_packet_sha256 !== campaign.feature_packet_sha256
    || brief.context_receipt_sha256 !== receiptSha256
    || brief.business_outcome !== campaign.business_outcome
    || brief.business_outcome !== publishedPayload.business_outcome
    || receipt.knowledge_snapshot_sha256 !== publishedPayload.knowledge_snapshot_sha256
    || receipt.capability_snapshot_sha256 !== publishedPayload.capability_snapshot_sha256
    || canonicalJson(receipt.marketing_context ?? null) !== canonicalJson(marketingContext)
    || canonicalJson(publishedPayload.marketing_context ?? null) !== canonicalJson(marketingContext)
    || receipt.created_at !== brief.created_at
  ) {
    throw new HttpError(409, "marketing judgment strategy scope is invalid");
  }
  const hypotheses = requireArray(brief.hypotheses, "strategy hypotheses", 2, 8);
  const controls = hypotheses.filter((hypothesis) => hypothesis?.role === "control");
  if (controls.length !== 1) throw new HttpError(409, "strategy requires exactly one control");
  const referenceSnapshot = publishedPayload.reference_snapshot ?? null;
  let referenceIds = new Set();
  if (referenceSnapshot !== null) {
    const snapshot = requireObject(referenceSnapshot, "published reference snapshot");
    if (
      await canonicalSha256(snapshot) !== publishedPayload.reference_snapshot_sha256
      || snapshot.schema_version !== "trace.reference-research.v1"
      || snapshot.campaign_id !== campaign.campaign_id
      || snapshot.feature_packet_sha256 !== campaign.feature_packet_sha256
      || snapshot.quarantine !== true
    ) {
      throw new HttpError(409, "strategy reference snapshot binding is invalid");
    }
    referenceIds = new Set(requireArray(snapshot.sources, "reference sources", 2, 16)
      .map((source) => safeId(source?.source_id, "reference source_id")));
  } else if (publishedPayload.reference_snapshot_sha256 != null) {
    throw new HttpError(409, "strategy reference snapshot digest has no snapshot");
  }
  validateHypothesisEvidence(hypotheses, publishedPayload.feature_packet, referenceIds);
  const experiment = requireObject(brief.experiment, "strategy experiment");
  const experimentId = safeId(experiment.experiment_id, "experiment_id");
  const activated = requireArray(
    experiment.activated_hypothesis_ids,
    "activated hypotheses",
    2,
    8,
  );
  const hypothesisIds = new Set(hypotheses.map((hypothesis) => safeId(
    hypothesis?.hypothesis_id,
    "hypothesis_id",
  )));
  if (activated.some((hypothesisId) => !hypothesisIds.has(hypothesisId))) {
    throw new HttpError(409, "experiment activates an unknown hypothesis");
  }
  const controlId = safeId(controls[0].hypothesis_id, "control hypothesis_id");
  if (!activated.includes(controlId) || new Set(activated).size !== activated.length) {
    throw new HttpError(409, "experiment must activate one unique control portfolio");
  }
  validateOutcomeDefinition(experiment.primary_outcome);
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
    strategy_brief_id: brief.brief_id,
    strategy_brief_sha256: briefSha256,
    experiment_id: experimentId,
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
      `INSERT INTO hosted_marketing_strategy_briefs
        (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
         brief_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      brief.brief_id,
      campaign.campaign_id,
      receipt.receipt_id,
      brief.schema_version,
      canonicalJson(brief),
      briefSha256,
      brief.created_at,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_experiments
        (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
         registration_json, registration_sha256, created_at, updated_at)
       VALUES (?, ?, ?, 'registered', ?, ?, ?, ?, ?)`,
    ).bind(
      experimentId,
      campaign.campaign_id,
      brief.brief_id,
      experiment.primary_outcome?.scope,
      canonicalJson(experiment),
      await canonicalSha256(experiment),
      now,
      now,
    ),
  ];
  for (const hypothesis of hypotheses) {
    const hypothesisId = safeId(hypothesis.hypothesis_id, "hypothesis_id");
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_hypotheses
          (hypothesis_id, campaign_id, strategy_brief_id, portfolio_role, hypothesis_json,
           hypothesis_sha256, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        hypothesisId,
        campaign.campaign_id,
        brief.brief_id,
        hypothesis.role,
        canonicalJson(hypothesis),
        await canonicalSha256(hypothesis),
        now,
      ),
    );
    if (activated.includes(hypothesisId)) {
      statements.push(
        env.DB.prepare(
          `INSERT INTO hosted_marketing_experiment_arms
            (arm_id, experiment_id, hypothesis_id, treatment_json, treatment_sha256,
             allocation_weight, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)`,
        ).bind(
          `${experimentId}.${hypothesisId}`,
          experimentId,
          hypothesisId,
          canonicalJson(hypothesis),
          await canonicalSha256(hypothesis),
          now,
        ),
      );
    }
  }
  statements.push(
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'experiment_registered', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'strategy_requested'
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
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      campaign.mode === "shadow" ? "shadow_strategy_completed" : "strategy_completed",
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaign.campaign_id}:strategy:${briefSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", storedResultJson, now),
  );
  const results = await env.DB.batch(statements);
  const campaignUpdate = results.at(-3);
  const completion = results.at(-1);
  if (campaignUpdate?.meta?.changes !== 1 || completion?.meta?.changes !== 1) {
    throw new HttpError(409, "marketing judgment completion lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    state: "experiment_registered",
    strategy_brief_id: brief.brief_id,
    experiment_id: experimentId,
  };
}

async function currentMarketingContextProjection(database, campaign, now) {
  const snapshotId = campaign.marketing_context_snapshot_id;
  const snapshotSha256 = campaign.marketing_context_snapshot_sha256;
  if (snapshotId == null && snapshotSha256 == null) return null;
  if (snapshotId == null || snapshotSha256 == null) {
    throw new HttpError(409, "campaign marketing context binding is incomplete");
  }
  const row = await database.prepare(
    `SELECT snapshot_json, snapshot_sha256, expires_at
     FROM hosted_marketing_context_snapshots
     WHERE snapshot_id = ? AND account_id = ? AND snapshot_sha256 = ?`,
  ).bind(snapshotId, campaign.account_id, snapshotSha256).first();
  if (!row || Date.parse(row.expires_at) <= Date.parse(now)) {
    throw new HttpError(409, "campaign marketing context binding is expired or invalid");
  }
  let snapshot;
  try {
    snapshot = requireObject(JSON.parse(row.snapshot_json), "campaign marketing context snapshot");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "campaign marketing context snapshot is invalid");
  }
  if (
    snapshot.schema_version !== "trace.marketing-context.v1"
    || snapshot.snapshot_id !== snapshotId
    || snapshot.account_id !== campaign.account_id
    || await canonicalSha256(snapshot) !== snapshotSha256
  ) {
    throw new HttpError(409, "campaign marketing context snapshot binding is invalid");
  }
  const customerSignals = requireArray(snapshot.customer_signals, "context customer signals", 1, 24);
  return {
    schema_version: "trace.marketing-context-projection.v1",
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: snapshotSha256,
    account_id: snapshot.account_id,
    brand_guardrails: requireArray(snapshot.brand_guardrails, "context brand guardrails", 1, 16),
    audience_context: requireArray(snapshot.audience_context, "context audience", 1, 16),
    channel_policy_ids: requireArray(snapshot.channel_policy_ids ?? [], "context channel policies", 0, 16),
    customer_signals: customerSignals,
    expires_at: snapshot.expires_at,
  };
}

async function finishFailedJudgment(
  env,
  task,
  callback,
  worker,
  campaign,
  storedResultJson,
  now,
) {
  const nextRevision = Number(campaign.projection_revision) + 1;
  const eventDetail = {
    campaign_id: campaign.campaign_id,
    task_id: task.task_id,
    status: callback.result.status,
    failure_code: callback.result.failure_code,
  };
  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'failed', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'strategy_requested'
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
       VALUES (?, ?, ?, ?, ?, 'shadow_strategy_failed', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaign.campaign_id}:strategy-failed:${task.task_id}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, callback.result.status, storedResultJson, now),
  ]);
  if (results[0]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
    throw new HttpError(409, "marketing judgment failure lost its state race");
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

function publishedJudgmentPayload(task) {
  try {
    const payload = JSON.parse(task.task_json)?.payload;
    return requireObject(payload, "published marketing judgment payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "published marketing judgment payload is invalid");
  }
}

function validateHypothesisEvidence(hypotheses, featurePacket, allowedReferenceIds) {
  const packet = requireObject(featurePacket, "published feature packet");
  const claims = requireArray(packet.claims, "published feature claims", 1, 64);
  const supportedClaimIds = new Set(claims
    .filter((claim) => [
      "source_supported",
      "build_bound",
      "installed_confirmed",
    ].includes(claim?.status))
    .map((claim) => safeId(claim?.claim_id, "published claim_id")));
  for (const hypothesis of hypotheses) {
    const claimIds = requireArray(hypothesis?.claim_ids, "hypothesis claim_ids", 1, 16)
      .map((claimId) => safeId(claimId, "hypothesis claim_id"));
    if (claimIds.some((claimId) => !supportedClaimIds.has(claimId))) {
      throw new HttpError(409, "strategy uses an unsupported feature claim");
    }
    if (
      !Array.isArray(hypothesis?.reference_ids)
      || hypothesis.reference_ids.some((referenceId) => !allowedReferenceIds.has(referenceId))
    ) {
      throw new HttpError(409, "shadow strategy breached the external reference quarantine");
    }
  }
}

function validateOutcomeDefinition(value) {
  const outcome = requireObject(value, "primary outcome");
  if (![
    "first_open",
    "feature_start",
    "generation_completed",
    "scheduling_completed",
    "setup_completed",
  ].includes(outcome.name)) {
    throw new HttpError(409, "primary outcome is not a versioned Trace product event");
  }
  if (outcome.scope === "direct_response_attribution" && outcome.causal_estimand != null) {
    throw new HttpError(409, "direct-response attribution cannot claim a causal estimand");
  }
  if (
    outcome.scope === "estimated_treatment_effect"
    && (typeof outcome.causal_estimand !== "string" || !outcome.causal_estimand.trim())
  ) {
    throw new HttpError(409, "estimated treatment effect requires a causal estimand");
  }
  if (![
    "direct_response_attribution",
    "estimated_treatment_effect",
  ].includes(outcome.scope)) {
    throw new HttpError(409, "primary outcome scope is invalid");
  }
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
